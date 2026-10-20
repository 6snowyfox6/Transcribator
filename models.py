"""Модели распознавания речи: требования и подбор под конкретную машину.

Все числа — замер на RTX 4050 Laptop (6 ГБ), запись 40 секунд, русская речь.

«Скорость» — во сколько раз распознавание быстрее реального времени, без
учёта загрузки модели и разделения говорящих: 30× означает, что час записи
распознаётся примерно за две минуты. На деле к этому добавляется разделение
говорящих, поэтому от начала до готового документа выходит примерно вдвое
дольше.

Видеопамять указана с учётом разделения говорящих: вторая модель живёт в
памяти рядом с первой и добавляет к расходу около 1.8 ГБ.

Любопытная деталь: large-v3-turbo быстрее не только large-v3, но и вчетверо
меньшей модели small — у turbo всего четыре слоя декодера, а именно декодер
определяет скорость на коротких записях.
"""

from __future__ import annotations

import json
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
MACHINE_FILE = PROJECT_DIR / "machine.json"

# Запас сверх замера: на машине с ровно таким объёмом памяти модель ещё
# помещается, но без единого мегабайта свободного места работать неприятно.
HEADROOM_MB = 150

SPECS: dict[str, dict] = {
    "large-v3": {
        "note": "самое точное распознавание",
        "disk_mb": 2948,
        "vram_mb": 4472,
        "vram_diarization_mb": 5328,
        "speed": 11.8,
    },
    "large-v3-turbo": {
        "note": "почти так же точно и вдвое быстрее",
        "disk_mb": 1547,
        "vram_mb": 2787,
        "vram_diarization_mb": 4678,
        "speed": 30.5,
    },
    "medium": {
        "note": "если видеопамяти мало",
        "disk_mb": 1460,
        "vram_mb": 2395,
        "vram_diarization_mb": 4091,
        "speed": 16.9,
    },
    "small": {
        "note": "черновой просмотр",
        "disk_mb": 464,
        "vram_mb": 859,
        "vram_diarization_mb": 2747,
        "speed": 27.6,
    },
}

# Порядок от лучшего качества к худшему — в нём же идёт подбор.
BY_QUALITY = ["large-v3", "large-v3-turbo", "medium", "small"]


def label(name: str) -> str:
    """Строка для выпадающего списка: «large-v3 — самое точное распознавание»."""
    spec = SPECS.get(name)
    return f"{name} — {spec['note']}" if spec else name


def needs_mb(name: str, diarization: bool = True) -> int:
    spec = SPECS.get(name)
    if not spec:
        return 0
    return spec["vram_diarization_mb"] if diarization else spec["vram_mb"]


def fits(name: str, vram_mb: int, diarization: bool = True) -> bool:
    return vram_mb >= needs_mb(name, diarization) + HEADROOM_MB


def recommend(vram_mb: int, gpu: bool = True) -> str:
    """Лучшая модель, которая поместится в видеопамять.

    Без видеокарты берём самую лёгкую: на процессоре large-v3 расшифровывает
    час записи несколько часов, что бессмысленно.
    """
    if not gpu:
        return "small"
    for name in BY_QUALITY:
        if fits(name, vram_mb):
            return name
    return "small"


def machine() -> dict:
    """Сведения о машине, записанные установщиком. Пусто — значит неизвестно."""
    if not MACHINE_FILE.is_file():
        return {}
    try:
        # utf-8-sig: файл пишет PowerShell, а он всегда ставит BOM.
        return json.loads(MACHINE_FILE.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}


def gigabytes(megabytes: int) -> str:
    return f"{megabytes / 1024:.1f} ГБ".replace(".", ",")


def requirements_text(name: str, diarization: bool = True) -> str:
    """Одна строка требований под выпадающим списком моделей."""
    spec = SPECS.get(name)
    if not spec:
        return ""

    need = needs_mb(name, diarization)
    parts = [
        f"на диске {gigabytes(spec['disk_mb'])}",
        f"видеопамяти {gigabytes(need)}",
        f"скорость ~{spec['speed']:.0f}× (на RTX 4050)",
    ]
    line = " · ".join(parts)

    info = machine()
    if not info:
        return line
    if not info.get("gpu"):
        return line + " · у вас нет видеокарты NVIDIA: пойдёт на процессоре, медленно"

    vram = info.get("vram_mb", 0)
    if fits(name, vram, diarization):
        return line + f" · у вас {gigabytes(vram)} — подходит"
    return line + f" · у вас {gigabytes(vram)} — не хватит, выберите модель полегче"
