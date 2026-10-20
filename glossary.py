"""Словарь терминов: подсказка модели при распознавании и правка после неё.

Зачем. Whisper уверенно путает редкие слова — фамилии, названия, профессиональный
жаргон. Словарь работает с двух сторон: список терминов уходит в модель как
`hotwords` (она начинает их ожидать), а то, что всё-таки распозналось неверно,
правится здесь сравнением по похожести.

Формат файла (glossary.txt), одна строка — одна запись:

    # строки с решёткой — комментарии
    EBITDA
    гносеогенная
    Рац Наталья Александровна
    гносиогенная => гносеогенная     # явная замена, применяется всегда

Про русские окончания. Наивное сравнение строк здесь опасно: «Платона» похоже
на «Платон» на 92%, и замена по порогу превратила бы родительный падеж в
именительный. Поэтому сравнивается только основа длиной в сам термин, а
окончание исходного слова сохраняется: «Платона» → «Платон» + «а» = «Платона»,
то есть ничего не меняется, а «гносиогенная» → «гносеогенная».
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
GLOSSARY_FILE = PROJECT_DIR / "glossary.txt"

# Короткие слова сравнивать бессмысленно: у них любая опечатка меняет смысл,
# а похожих слов слишком много.
MIN_LENGTH = 5
# Насколько основа должна совпасть, чтобы считать это тем же словом.
THRESHOLD = 88.0
# Сколько букв окончания допускаем сверх длины термина («Платон» + «ами»).
MAX_ENDING = 3
# Сколько букв в начале обязаны совпасть. При ослышке начало слова обычно
# сохраняется, а вот «биологическое» и «мифологическое» похожи только
# хвостом — и без этого условия одно подменялось другим.
MIN_PREFIX = 3
# hotwords в faster-whisper обрезаются примерно на 224 токенах.
MAX_HOTWORDS = 60

EXAMPLE = """\
# Словарь терминов Транскрибатора.
#
# Одна строка — одно слово или имя, которое модель склонна путать.
# Эти слова подсказываются модели при распознавании и правятся после него.
#
# Можно задать и явную замену, она применяется всегда:
#     гносиогенная => гносеогенная
#
# Строки, начинающиеся с решётки, пропускаются.

"""

WORD = re.compile(r"[^\W\d_]+(?:-[^\W\d_]+)*", re.UNICODE)


@dataclass
class Glossary:
    terms: list[str] = field(default_factory=list)
    replacements: dict[str, str] = field(default_factory=dict)

    def __bool__(self) -> bool:
        return bool(self.terms or self.replacements)


def load(path: Path | str | None = None) -> Glossary:
    path = Path(path) if path else GLOSSARY_FILE
    glossary = Glossary()
    if not path.is_file():
        return glossary

    try:
        # utf-8-sig: файл мог быть сохранён Блокнотом, а он ставит BOM.
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return glossary

    for line in lines:
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        if "=>" in line:
            wrong, _, right = line.partition("=>")
            wrong, right = wrong.strip(), right.strip()
            if wrong and right:
                glossary.replacements[wrong.lower()] = right
                if right not in glossary.terms:
                    glossary.terms.append(right)
        elif line not in glossary.terms:
            glossary.terms.append(line)
    return glossary



def plural(count: int, one: str, few: str, many: str) -> str:
    """«1 слово», «3 слова», «5 слов» — по правилам русского счёта."""
    tail_hundred = count % 100
    tail = count % 10
    if 11 <= tail_hundred <= 14:
        word = many
    elif tail == 1:
        word = one
    elif 2 <= tail <= 4:
        word = few
    else:
        word = many
    return f"{count} {word}"


def hotwords(glossary: Glossary) -> str | None:
    """Строка подсказки для faster-whisper."""
    if not glossary.terms:
        return None
    return " ".join(glossary.terms[:MAX_HOTWORDS])


def _common_prefix(first: str, second: str) -> int:
    length = 0
    for left, right in zip(first, second):
        if left != right:
            break
        length += 1
    return length


def _match_case(sample: str, text: str) -> str:
    """Переносит регистр исходного слова на исправленное."""
    if sample.isupper() and len(sample) > 1:
        return text.upper()
    if sample[:1].isupper():
        return text[:1].upper() + text[1:]
    return text


def _fix_word(word: str, glossary: Glossary, scorer) -> str | None:
    """Исправленное слово или None, если менять нечего."""
    lowered = word.lower()

    explicit = glossary.replacements.get(lowered)
    if explicit is not None:
        return _match_case(word, explicit) if explicit.islower() else explicit

    if scorer is None or len(word) < MIN_LENGTH:
        return None

    best_term, best_score = None, THRESHOLD
    for term in glossary.terms:
        if len(term) < MIN_LENGTH or " " in term:
            continue
        if lowered == term.lower():
            return None  # уже написано правильно
        ending = word[len(term):]
        if len(ending) > MAX_ENDING or len(word) < len(term) - MAX_ENDING:
            continue
        prefix = _common_prefix(lowered, term.lower())
        # Расхождение только в последних буквах — это падежное окончание,
        # а не ошибка: «Аристотеля» не должно превратиться в «Аристотель»
        # (здесь окончание не дописывается, а заменяет мягкий знак).
        if prefix >= len(term) - 2:
            continue
        # А совпадение одним хвостом — не ослышка, а разные слова.
        if prefix < MIN_PREFIX:
            continue
        score = scorer(word[:len(term)].lower(), term.lower())
        if score > best_score:
            best_term, best_score = term, score

    if best_term is None:
        return None
    fixed = best_term + word[len(best_term):]
    # У обычного слова регистр берём у исходного, у имени или аббревиатуры
    # («Платон», «EBITDA») — оставляем написание из словаря.
    if best_term.islower():
        fixed = _match_case(word, fixed)
    return None if fixed == word else fixed


def correct(text: str, glossary: Glossary) -> tuple[str, list[tuple[str, str]]]:
    """Правит текст по словарю. Возвращает (текст, список замен)."""
    if not glossary:
        return text, []

    try:
        from rapidfuzz import fuzz

        scorer = fuzz.ratio
    except ImportError:
        # Без RapidFuzz остаются явные замены «ошибочно => правильно»:
        # они и так точные, а нечёткое сравнение просто отключается.
        scorer = None

    changes: list[tuple[str, str]] = []

    def replace(match: re.Match) -> str:
        word = match.group(0)
        fixed = _fix_word(word, glossary, scorer)
        if fixed is None:
            return word
        changes.append((word, fixed))
        return fixed

    return WORD.sub(replace, text), changes


def ensure_example(path: Path | str | None = None) -> Path:
    """Создаёт файл-заготовку, если словаря ещё нет."""
    path = Path(path) if path else GLOSSARY_FILE
    if not path.exists():
        try:
            path.write_text(EXAMPLE, encoding="utf-8")
        except OSError:
            pass
    return path
