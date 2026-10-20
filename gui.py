"""Графический интерфейс к transcribe.py.

Сама расшифровка запускается отдельным процессом (`transcribe.py --progress-json`),
а окно лишь читает поток событий. Так интерфейс не подвисает на загрузке модели,
а кнопка «Остановить» гарантированно прекращает работу.

Запуск: .venv\\Scripts\\pythonw.exe gui.py
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import glossary
from first_run import run_wizard, token_is_ready
from models import BY_QUALITY, label as model_label, machine, recommend, requirements_text
from tkutil import enable_clipboard_shortcuts
from transcribe import HISTORY_FILE, MEDIA_SUFFIXES, PROJECT_DIR, read_history, timecode

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD

    ROOT_CLASS: type = TkinterDnD.Tk
except ImportError:  # перетаскивание необязательно, кнопки работают всегда
    DND_FILES = None
    ROOT_CLASS = tk.Tk

SETTINGS_FILE = PROJECT_DIR / "gui_settings.json"

LANGUAGES = [
    ("определить автоматически", None),
    ("русский", "ru"),
    ("английский", "en"),
    ("немецкий", "de"),
    ("французский", "fr"),
    ("испанский", "es"),
    ("украинский", "uk"),
]

MODELS = [(model_label(name), name) for name in BY_QUALITY]

SPEAKERS = [("определить автоматически", None)] + [(str(n), n) for n in range(2, 11)]

STATUS_WAITING = "в очереди"
STATUS_RUNNING = "обрабатывается"
STATUS_DONE = "готово"
STATUS_ERROR = "ошибка"


def human_time(stamp: str) -> str:
    """2026-09-12T14:33:05 → 12.09 14:33"""
    try:
        moment = datetime.fromisoformat(stamp)
    except ValueError:
        return stamp
    return moment.strftime("%d.%m %H:%M")


def labels(pairs: list[tuple[str, object]]) -> list[str]:
    return [label for label, _ in pairs]


def value_of(pairs: list[tuple[str, object]], label: str):
    for text, value in pairs:
        if text == label:
            return value
    return None


class App:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.files: list[Path] = []
        self.item_paths: dict[str, Path] = {}
        self.history_records: dict[str, dict] = {}
        self.wizard_shown = False
        self.events: queue.Queue = queue.Queue()
        self.process: subprocess.Popen | None = None
        self.worker: threading.Thread | None = None
        self.stopping = False
        self.current_index = 0
        self.total_files = 0
        self.file_started = 0.0

        root.title("Транскрибатор — расшифровка аудио и видео")
        root.minsize(780, 560)
        self.center(940, 720)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(0, weight=1)

        self.notebook = notebook = ttk.Notebook(root)
        notebook.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
        notebook.bind("<<NotebookTabChanged>>", lambda _event: self.refresh_history())

        work = ttk.Frame(notebook)
        notebook.add(work, text="  Расшифровка  ")
        work.columnconfigure(0, weight=1)
        work.rowconfigure(0, weight=3)
        work.rowconfigure(2, weight=2)

        history = ttk.Frame(notebook)
        notebook.add(history, text="  История  ")
        history.columnconfigure(0, weight=1)
        history.rowconfigure(0, weight=1)

        words = ttk.Frame(notebook)
        notebook.add(words, text="  Словарь  ")
        words.columnconfigure(0, weight=1)
        words.rowconfigure(1, weight=1)

        self._build_files(work)
        self._build_settings(work)
        self._build_run(work)
        self._build_history(history)
        self._build_glossary(words)

        self.load_settings()
        self.sync_token_state()
        self.refresh_history()
        enable_clipboard_shortcuts(root)
        root.after(300, self.maybe_first_run)
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.after(100, self.drain_events)

    # ------------------------------------------------------------------ #
    # Сборка окна
    # ------------------------------------------------------------------ #

    def center(self, width: int, height: int) -> None:
        """Ставит окно по центру, не вылезая за край экрана."""
        screen_h = self.root.winfo_screenheight()
        height = min(height, screen_h - 80)  # запас на панель задач
        x = max((self.root.winfo_screenwidth() - width) // 2, 0)
        y = max((screen_h - height) // 2 - 20, 0)
        self.root.geometry(f"{width}x{height}+{x}+{y}")

    def _build_files(self, root) -> None:
        box = ttk.LabelFrame(root, text="Записи", padding=8)
        box.grid(row=0, column=0, sticky="nsew", padx=10, pady=(10, 6))
        box.columnconfigure(0, weight=1)
        box.rowconfigure(0, weight=1)

        self.tree = ttk.Treeview(box, columns=("name", "status"), show="headings",
                                 selectmode="extended", height=7)
        self.tree.heading("name", text="Файл", anchor="w")
        self.tree.heading("status", text="Состояние", anchor="w")
        self.tree.column("name", width=520, anchor="w")
        self.tree.column("status", width=130, anchor="w", stretch=False)
        self.tree.grid(row=0, column=0, sticky="nsew")

        scroll = ttk.Scrollbar(box, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        scroll.grid(row=0, column=1, sticky="ns")

        self.tree.tag_configure("done", foreground="#1a7f37")
        self.tree.tag_configure("error", foreground="#b42318")
        self.tree.tag_configure("running", foreground="#0b5cad")

        buttons = ttk.Frame(box)
        buttons.grid(row=0, column=2, sticky="n", padx=(8, 0))
        ttk.Button(buttons, text="Добавить файлы…", command=self.add_files).grid(
            row=0, column=0, sticky="ew", pady=2)
        ttk.Button(buttons, text="Добавить папку…", command=self.add_folder).grid(
            row=1, column=0, sticky="ew", pady=2)
        ttk.Button(buttons, text="Убрать выбранные", command=self.remove_selected).grid(
            row=2, column=0, sticky="ew", pady=(12, 2))
        ttk.Button(buttons, text="Очистить список", command=self.clear_files).grid(
            row=3, column=0, sticky="ew", pady=2)

        hint = ("Перетащите сюда файлы или папку"
                if DND_FILES else "Добавьте файлы кнопками справа")
        self.hint = ttk.Label(box, text=hint, foreground="#6b7280")
        self.hint.grid(row=1, column=0, columnspan=3, sticky="w", pady=(6, 0))

        if DND_FILES:
            self.tree.drop_target_register(DND_FILES)
            self.tree.dnd_bind("<<Drop>>", self.on_drop)

    def _build_settings(self, root) -> None:
        box = ttk.LabelFrame(root, text="Настройки", padding=8)
        box.grid(row=1, column=0, sticky="ew", padx=10, pady=6)
        box.columnconfigure(1, weight=1)
        box.columnconfigure(3, weight=1)

        ttk.Label(box, text="Папка результатов:").grid(row=0, column=0, sticky="w", pady=3)
        self.out_dir = tk.StringVar(value=str(PROJECT_DIR / "output"))
        ttk.Entry(box, textvariable=self.out_dir).grid(
            row=0, column=1, columnspan=3, sticky="ew", padx=6, pady=3)
        ttk.Button(box, text="Выбрать…", command=self.pick_out_dir).grid(
            row=0, column=4, sticky="ew", pady=3)

        ttk.Label(box, text="Язык записи:").grid(row=1, column=0, sticky="w", pady=3)
        self.language = tk.StringVar(value=LANGUAGES[0][0])
        ttk.Combobox(box, textvariable=self.language, values=labels(LANGUAGES),
                     state="readonly").grid(row=1, column=1, sticky="ew", padx=6, pady=3)

        ttk.Label(box, text="Модель:").grid(row=1, column=2, sticky="e", pady=3)
        self.model = tk.StringVar(value=MODELS[0][0])
        self.model_box = ttk.Combobox(box, textvariable=self.model,
                                      values=labels(MODELS), state="readonly")
        self.model_box.grid(row=1, column=3, columnspan=2, sticky="ew", padx=6, pady=3)
        self.model_box.bind("<<ComboboxSelected>>",
                            lambda _event: self.sync_requirements())

        self.requirements = ttk.Label(box, text="", foreground="#6b7280",
                                      wraplength=760)
        self.requirements.grid(row=2, column=0, columnspan=5, sticky="w", pady=(0, 4))

        self.diarize = tk.BooleanVar(value=True)
        self.diarize_box = ttk.Checkbutton(box, text="Разделять говорящих",
                                           variable=self.diarize,
                                           command=self.sync_speakers)
        self.diarize_box.grid(row=3, column=0, sticky="w", pady=3)
        self.wizard_button = ttk.Button(box, text="Доступ к модели…", width=18,
                                        command=self.open_wizard)
        self.wizard_button.grid(row=3, column=1, sticky="w", padx=6, pady=3)

        ttk.Label(box, text="Сколько говорящих:").grid(row=3, column=2, sticky="e", pady=3)
        self.speakers = tk.StringVar(value=SPEAKERS[0][0])
        self.speakers_box = ttk.Combobox(box, textvariable=self.speakers,
                                        values=labels(SPEAKERS), state="readonly")
        self.speakers_box.grid(row=3, column=3, columnspan=2, sticky="ew", padx=6, pady=3)

        ttk.Label(box, text="Имена и термины:").grid(row=4, column=0, sticky="w", pady=3)
        self.prompt = tk.StringVar()
        ttk.Entry(box, textvariable=self.prompt).grid(
            row=4, column=1, columnspan=4, sticky="ew", padx=6, pady=3)
        ttk.Label(box, text="Подсказка модели, например: «Совещание. Участники: Иванов, "
                            "Петрова. Термины: EBITDA, Транскрибатор.»",
                  foreground="#6b7280", wraplength=760).grid(
            row=5, column=0, columnspan=5, sticky="w")

    def _build_run(self, root) -> None:
        box = ttk.Frame(root, padding=(10, 0, 10, 10))
        box.grid(row=2, column=0, sticky="nsew")
        box.columnconfigure(0, weight=1)
        box.rowconfigure(3, weight=1)

        bar = ttk.Frame(box)
        bar.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        bar.columnconfigure(2, weight=1)
        self.start_button = ttk.Button(bar, text="Начать расшифровку", command=self.start)
        self.start_button.grid(row=0, column=0)
        self.stop_button = ttk.Button(bar, text="Остановить", command=self.stop,
                                     state="disabled")
        self.stop_button.grid(row=0, column=1, padx=6)
        ttk.Button(bar, text="Открыть папку результатов",
                   command=self.open_out_dir).grid(row=0, column=3, sticky="e")

        self.progress = ttk.Progressbar(box, mode="determinate", maximum=1000)
        self.progress.grid(row=1, column=0, sticky="ew")

        self.status = ttk.Label(box, text="Готов к работе")
        self.status.grid(row=2, column=0, sticky="w", pady=(4, 6))

        log_box = ttk.LabelFrame(box, text="Журнал", padding=4)
        log_box.grid(row=3, column=0, sticky="nsew")
        log_box.columnconfigure(0, weight=1)
        log_box.rowconfigure(0, weight=1)
        self.log = tk.Text(log_box, height=6, wrap="word", state="disabled",
                          background="#f7f7f8", relief="flat",
                          font=("Segoe UI", 9))
        self.log.grid(row=0, column=0, sticky="nsew")
        log_scroll = ttk.Scrollbar(log_box, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=log_scroll.set)
        log_scroll.grid(row=0, column=1, sticky="ns")
        self.log.tag_configure("error", foreground="#b42318")
        self.log.tag_configure("ok", foreground="#1a7f37")
        self.log.tag_configure("dim", foreground="#9aa0a6")


    def _build_history(self, root) -> None:
        columns = ("time", "name", "duration", "speakers", "model", "spent", "status")
        titles = {
            "time": ("Когда", 110),
            "name": ("Запись", 300),
            "duration": ("Длина", 70),
            "speakers": ("Спикеров", 80),
            "model": ("Модель", 120),
            "spent": ("Затрачено", 90),
            "status": ("Итог", 80),
        }
        self.history = ttk.Treeview(root, columns=columns, show="headings", height=14)
        for key in columns:
            title, width = titles[key]
            self.history.heading(key, text=title, anchor="w")
            self.history.column(key, width=width, anchor="w",
                                stretch=(key == "name"))
        self.history.grid(row=0, column=0, sticky="nsew", padx=8, pady=(8, 4))
        self.history.tag_configure("error", foreground="#b42318")
        self.history.tag_configure("missing", foreground="#9aa0a6")
        self.history.bind("<Double-1>", lambda _event: self.open_document())

        scroll = ttk.Scrollbar(root, orient="vertical", command=self.history.yview)
        self.history.configure(yscrollcommand=scroll.set)
        scroll.grid(row=0, column=1, sticky="ns", pady=(8, 4))

        buttons = ttk.Frame(root)
        buttons.grid(row=1, column=0, columnspan=2, sticky="ew", padx=8, pady=(0, 8))
        buttons.columnconfigure(3, weight=1)
        ttk.Button(buttons, text="Открыть документ",
                   command=self.open_document).grid(row=0, column=0)
        ttk.Button(buttons, text="Показать в папке",
                   command=self.show_in_folder).grid(row=0, column=1, padx=6)
        ttk.Button(buttons, text="Расшифровать заново",
                   command=self.requeue).grid(row=0, column=2)
        ttk.Button(buttons, text="Очистить историю",
                   command=self.clear_history).grid(row=0, column=4, sticky="e")

        self.history_hint = ttk.Label(
            root, text="", foreground="#6b7280")
        self.history_hint.grid(row=2, column=0, columnspan=2, sticky="w", padx=8,
                               pady=(0, 8))



    def _build_glossary(self, root) -> None:
        hint = (
            "Слова, которые модель склонна путать: фамилии, названия, термины. "
            "Одна строка — одно слово.\n"
            "Словарь работает дважды: подсказывается модели при распознавании "
            "и правит то, что всё же распозналось неверно.\n"
            "Строку вида «ошибочно => правильно» программа применяет всегда, "
            "строки с решёткой — комментарии."
        )
        ttk.Label(root, text=hint, foreground="#6b7280", wraplength=860,
                  justify="left").grid(row=0, column=0, columnspan=2, sticky="w",
                                       padx=8, pady=(8, 6))

        self.glossary_text = tk.Text(root, wrap="none", undo=True,
                                     font=("Consolas", 10))
        self.glossary_text.grid(row=1, column=0, sticky="nsew", padx=(8, 0))
        scroll = ttk.Scrollbar(root, orient="vertical",
                               command=self.glossary_text.yview)
        self.glossary_text.configure(yscrollcommand=scroll.set)
        scroll.grid(row=1, column=1, sticky="ns", padx=(0, 8))

        buttons = ttk.Frame(root)
        buttons.grid(row=2, column=0, columnspan=2, sticky="ew", padx=8, pady=8)
        buttons.columnconfigure(2, weight=1)
        ttk.Button(buttons, text="Сохранить словарь",
                   command=self.save_glossary).grid(row=0, column=0)
        ttk.Button(buttons, text="Вернуть сохранённое",
                   command=self.load_glossary).grid(row=0, column=1, padx=6)
        self.glossary_status = ttk.Label(buttons, text="", foreground="#6b7280")
        self.glossary_status.grid(row=0, column=3, sticky="e")

        self.load_glossary()

    def load_glossary(self) -> None:
        path = glossary.ensure_example()
        try:
            text = path.read_text(encoding="utf-8-sig")
        except OSError as error:
            text = ""
            self.glossary_status.configure(text=f"не прочитать: {error}",
                                           foreground="#b42318")
        self.glossary_text.delete("1.0", "end")
        self.glossary_text.insert("1.0", text)
        self.show_glossary_size()

    def save_glossary(self) -> None:
        text = self.glossary_text.get("1.0", "end-1c")
        try:
            glossary.GLOSSARY_FILE.write_text(text, encoding="utf-8")
        except OSError as error:
            messagebox.showerror("Словарь", f"Не удалось сохранить:\n{error}")
            return
        self.show_glossary_size(saved=True)

    def show_glossary_size(self, saved: bool = False) -> None:
        loaded = glossary.load()
        total = len(loaded.terms)
        note = "сохранено · " if saved else ""
        self.glossary_status.configure(
            text=f"{note}терминов: {total}", foreground="#1a7f37" if saved else "#6b7280")

    # ------------------------------------------------------------------ #
    # Доступ к модели разделения говорящих
    # ------------------------------------------------------------------ #

    def maybe_first_run(self) -> None:
        """Первый запуск без токена — показываем мастер. Отказ не повторяем."""
        if self.wizard_shown or token_is_ready():
            return
        self.wizard_shown = True
        if not run_wizard(self.root):
            self.write_log(
                "Разделение говорящих недоступно: доступ к модели не настроен. "
                "Настроить можно кнопкой «Настроить доступ…».", "dim")
        self.sync_token_state()
        self.save_settings()

    def open_wizard(self) -> None:
        self.wizard_shown = True
        if run_wizard(self.root):
            self.diarize.set(True)
            self.write_log("Доступ к модели разделения говорящих настроен.", "ok")
        self.sync_token_state()
        self.save_settings()

    # ------------------------------------------------------------------ #
    # История
    # ------------------------------------------------------------------ #

    def refresh_history(self) -> None:
        if not hasattr(self, "history"):
            return
        self.history.delete(*self.history.get_children())
        self.history_records = {}

        records = read_history()
        for record in records:
            output = record.get("output")
            missing = record.get("status") == "ok" and output and not Path(output).exists()
            speakers = record.get("speakers")
            values = (
                human_time(record.get("time", "")),
                Path(record.get("source", "")).name,
                timecode(record["duration"]) if record.get("duration") else "",
                str(speakers) if speakers else "—",
                record.get("model", ""),
                timecode(record["seconds"]) if record.get("seconds") else "",
                "ошибка" if record.get("status") == "error" else
                ("файл удалён" if missing else "готово"),
            )
            tag = "error" if record.get("status") == "error" else (
                "missing" if missing else "")
            item = self.history.insert("", "end", values=values,
                                       tags=(tag,) if tag else ())
            self.history_records[item] = record

        total = len(records)
        done = sum(1 for r in records if r.get("status") == "ok")
        hours = sum(r.get("duration") or 0 for r in records if r.get("status") == "ok")
        self.history_hint.configure(
            text=(f"Всего записей: {total} · успешно: {done} · "
                  f"расшифровано звука: {timecode(hours)}")
            if total else "История пока пуста.")

    def selected_record(self) -> dict | None:
        selection = self.history.selection()
        if not selection:
            messagebox.showinfo("История", "Сначала выберите строку.")
            return None
        return self.history_records.get(selection[0])

    def open_document(self) -> None:
        record = self.selected_record()
        if not record:
            return
        if record.get("status") != "ok" or not record.get("output"):
            messagebox.showinfo(
                "История", record.get("message") or "Для этой записи документа нет.")
            return
        path = Path(record["output"])
        if not path.exists():
            messagebox.showwarning("История", f"Документ больше не найден:\n{path}")
            return
        os.startfile(str(path))  # noqa: S606

    def show_in_folder(self) -> None:
        record = self.selected_record()
        if not record:
            return
        path = Path(record.get("output") or record.get("source", ""))
        if not path.exists():
            messagebox.showwarning("История", f"Файл больше не найден:\n{path}")
            return
        subprocess.Popen(["explorer", "/select,", str(path)])

    def requeue(self) -> None:
        """Возвращает исходную запись в очередь на вкладке «Расшифровка»."""
        record = self.selected_record()
        if not record:
            return
        source = record.get("source", "")
        if not Path(source).exists():
            messagebox.showwarning("История", f"Исходная запись не найдена:\n{source}")
            return
        self.add_paths([source])
        messagebox.showinfo("История", "Запись добавлена в очередь на вкладке "
                                       "«Расшифровка».")

    def clear_history(self) -> None:
        if not messagebox.askokcancel(
                "Очистить историю",
                "Удалить все записи истории?\n\n"
                "Сами документы и записи останутся на месте."):
            return
        try:
            HISTORY_FILE.unlink(missing_ok=True)
        except OSError as error:
            messagebox.showerror("История", f"Не удалось очистить:\n{error}")
            return
        self.refresh_history()

    # ------------------------------------------------------------------ #
    # Список файлов
    # ------------------------------------------------------------------ #

    def add_paths(self, raw: list[str]) -> None:
        added = 0
        for item in raw:
            path = Path(item)
            if path.is_dir():
                found = sorted(p for p in path.rglob("*")
                               if p.suffix.lower() in MEDIA_SUFFIXES)
            elif path.is_file() and path.suffix.lower() in MEDIA_SUFFIXES:
                found = [path]
            else:
                continue
            for media in found:
                if media not in self.files:
                    self.files.append(media)
                    item = self.tree.insert("", "end",
                                            values=(media.name, STATUS_WAITING))
                    self.item_paths[item] = media
                    added += 1
        if added:
            self.write_log(f"Добавлено записей: {added}")

    def add_files(self) -> None:
        patterns = " ".join(f"*{s}" for s in sorted(MEDIA_SUFFIXES))
        chosen = filedialog.askopenfilenames(
            title="Выберите аудио или видео",
            filetypes=[("Аудио и видео", patterns), ("Все файлы", "*.*")],
        )
        self.add_paths(list(chosen))

    def add_folder(self) -> None:
        folder = filedialog.askdirectory(title="Выберите папку с записями")
        if folder:
            self.add_paths([folder])

    def on_drop(self, event) -> None:
        self.add_paths(list(self.tree.tk.splitlist(event.data)))

    def remove_selected(self) -> None:
        if self.process:
            return
        for item in self.tree.selection():
            path = self.item_paths.pop(item, None)
            if path in self.files:
                self.files.remove(path)
            self.tree.delete(item)

    def clear_files(self) -> None:
        if self.process:
            return
        self.files.clear()
        self.item_paths.clear()
        self.tree.delete(*self.tree.get_children())

    def row(self, index: int):
        """Строка таблицы по номеру файла в очереди (нумерация с единицы)."""
        children = self.tree.get_children()
        return children[index - 1] if 0 < index <= len(children) else None

    def set_status(self, index: int, text: str, tag: str = "") -> None:
        item = self.row(index)
        if item:
            self.tree.set(item, "status", text)
            self.tree.item(item, tags=(tag,) if tag else ())
            self.tree.see(item)

    # ------------------------------------------------------------------ #
    # Журнал и прогресс
    # ------------------------------------------------------------------ #

    def write_log(self, text: str, tag: str = "") -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text.rstrip() + "\n", tag)
        self.log.see("end")
        self.log.configure(state="disabled")

    def set_progress(self, fraction: float) -> None:
        done = max(self.current_index - 1, 0)
        overall = (done + fraction) / self.total_files if self.total_files else 0.0
        self.progress.configure(value=min(overall, 1.0) * 1000)

    # ------------------------------------------------------------------ #
    # Запуск и остановка
    # ------------------------------------------------------------------ #

    def build_command(self) -> list[str]:
        # sys.executable — тот же интерпретатор, под которым работает окно.
        # Так расшифровка находится и в окружении разработки (.venv), и после
        # установки, где Python лежит внутри папки программы.
        command = [sys.executable, str(PROJECT_DIR / "transcribe.py"), "--progress-json"]
        command += [str(path) for path in self.files]
        command += ["-o", self.out_dir.get().strip()]
        command += ["--model", value_of(MODELS, self.model.get())]

        language = value_of(LANGUAGES, self.language.get())
        if language:
            command += ["--language", language]

        if self.diarize.get():
            count = value_of(SPEAKERS, self.speakers.get())
            if count:
                command += ["--num-speakers", str(count)]
        else:
            command.append("--no-diarization")

        prompt = self.prompt.get().strip()
        if prompt:
            command += ["--initial-prompt", prompt]
        return command

    def start(self) -> None:
        if self.process:
            return
        if not self.files:
            messagebox.showinfo("Нет записей", "Сначала добавьте аудио или видео.")
            return

        out_dir = self.out_dir.get().strip()
        if not out_dir:
            messagebox.showwarning("Папка результатов", "Укажите, куда сохранять документы.")
            return
        try:
            Path(out_dir).mkdir(parents=True, exist_ok=True)
        except OSError as error:
            messagebox.showerror("Папка результатов", f"Не удалось создать папку:\n{error}")
            return

        self.save_settings()
        self.stopping = False
        self.current_index = 0
        self.total_files = len(self.files)
        self.progress.configure(value=0)

        for item in self.tree.get_children():
            self.tree.set(item, "status", STATUS_WAITING)
            self.tree.item(item, tags=())

        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.status.configure(text="Запуск…")
        self.write_log("")
        self.write_log(f"Запуск: {self.total_files} запис(ей)")

        environment = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self.process = subprocess.Popen(
            self.build_command(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(PROJECT_DIR),
            env=environment,
            encoding="utf-8",
            errors="replace",
            creationflags=flags,
        )
        self.worker = threading.Thread(target=self.pump, daemon=True)
        self.worker.start()

    def pump(self) -> None:
        """Читает оба потока дочернего процесса и складывает события в очередь."""
        process = self.process
        assert process is not None

        def read_stderr() -> None:
            for line in process.stderr:
                if line.strip():
                    self.events.put(("stderr", line.rstrip()))

        stderr_thread = threading.Thread(target=read_stderr, daemon=True)
        stderr_thread.start()

        for line in process.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                self.events.put(("event", json.loads(line)))
            except json.JSONDecodeError:
                self.events.put(("stderr", line))

        code = process.wait()
        stderr_thread.join(timeout=2)
        self.events.put(("exit", code))

    def stop(self) -> None:
        if not self.process:
            return
        self.stopping = True
        self.stop_button.configure(state="disabled")
        self.status.configure(text="Останавливаюсь…")
        self.process.terminate()
        # terminate() на Windows долетает не всегда — добиваем через секунду.
        self.root.after(1000, self.force_kill)

    def force_kill(self) -> None:
        if self.process and self.process.poll() is None:
            self.process.kill()

    # ------------------------------------------------------------------ #
    # Обработка событий
    # ------------------------------------------------------------------ #

    def drain_events(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "event":
                    self.handle_event(payload)
                elif kind == "stderr":
                    self.write_log(payload, "dim")
                else:
                    self.handle_exit(payload)
        except queue.Empty:
            pass
        self.root.after(100, self.drain_events)

    def handle_event(self, event: dict) -> None:
        name = event.get("event")

        if name == "queued":
            self.total_files = event["total"]

        elif name == "file_start":
            self.current_index = event["index"]
            self.file_started = time.monotonic()
            self.set_status(self.current_index, STATUS_RUNNING, "running")
            self.set_progress(0.0)
            self.status.configure(
                text=f"[{event['index']} из {event['total']}] {event['name']}")
            self.write_log(f"— {event['name']}")

        elif name == "duration":
            self.write_log(f"   длительность {event['text']}", "dim")

        elif name == "language":
            self.write_log(f"   язык: {event['code']} ({event['probability']:.0%})", "dim")

        elif name == "stage":
            self.status.configure(text=f"{self.prefix()}{event['text']}")
            if event["key"] in ("diarize", "cpu", "glossary"):
                self.write_log(f"   {event['text']}", "dim")

        elif name == "progress":
            fraction = event["fraction"]
            self.set_progress(fraction)
            elapsed = time.monotonic() - self.file_started
            eta = f", осталось ~{timecode(elapsed / fraction - elapsed)}" if fraction > 0.05 else ""
            self.status.configure(
                text=f"{self.prefix()}распознавание {fraction:.0%}{eta}")

        elif name == "file_done":
            self.set_status(self.current_index, STATUS_DONE, "done")
            speakers = event.get("speakers") or 0
            extra = f", говорящих: {speakers}" if speakers else ""
            self.write_log(
                f"   готово за {timecode(event['seconds'])}{extra}"
                f" → {Path(event['path']).name}", "ok")

        elif name == "file_error":
            if event.get("name"):
                self.set_status(self.current_index, STATUS_ERROR, "error")
            self.write_log(f"   ошибка: {event['message']}", "error")

        elif name == "finished":
            self.write_log(
                f"Итог: успешно {event['ok']}, с ошибками {event['failed']}",
                "error" if event["failed"] else "ok")
            self.refresh_history()

    def prefix(self) -> str:
        if not self.total_files:
            return ""
        return f"[{self.current_index} из {self.total_files}] "

    def handle_exit(self, code: int) -> None:
        self.process = None
        self.worker = None
        self.start_button.configure(state="normal")
        self.stop_button.configure(state="disabled")

        if self.stopping:
            self.status.configure(text="Остановлено")
            self.write_log("Остановлено пользователем", "error")
            for item in self.tree.get_children():
                if self.tree.set(item, "status") == STATUS_RUNNING:
                    self.tree.set(item, "status", STATUS_WAITING)
                    self.tree.item(item, tags=())
        elif code == 0:
            self.progress.configure(value=1000)
            self.status.configure(text="Готово")
        else:
            self.status.configure(text="Завершено с ошибками")

    # ------------------------------------------------------------------ #
    # Папки и настройки
    # ------------------------------------------------------------------ #

    def pick_out_dir(self) -> None:
        folder = filedialog.askdirectory(title="Куда сохранять документы",
                                         initialdir=self.out_dir.get() or str(PROJECT_DIR))
        if folder:
            self.out_dir.set(folder)

    def open_out_dir(self) -> None:
        folder = Path(self.out_dir.get().strip() or PROJECT_DIR / "output")
        if not folder.is_dir():
            messagebox.showinfo("Папка результатов", "Папка ещё не создана.")
            return
        os.startfile(str(folder))  # noqa: S606  (штатный способ открыть Проводник)

    def load_settings(self) -> None:
        if not SETTINGS_FILE.is_file():
            self.apply_machine_defaults()
            return
        try:
            data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        self.out_dir.set(data.get("out_dir") or self.out_dir.get())
        if data.get("language") in labels(LANGUAGES):
            self.language.set(data["language"])
        if data.get("model") in labels(MODELS):
            self.model.set(data["model"])
        if data.get("speakers") in labels(SPEAKERS):
            self.speakers.set(data["speakers"])
        self.diarize.set(bool(data.get("diarize", True)) and token_is_ready())
        self.wizard_shown = bool(data.get("wizard_shown", False))
        self.prompt.set(data.get("prompt", ""))
        self.sync_speakers()
        self.sync_requirements()


    def apply_machine_defaults(self) -> None:
        """Первый запуск: подбираем модель под видеокарту этой машины."""
        info = machine()
        if not info:
            self.sync_requirements()
            return
        name = recommend(info.get("vram_mb", 0), info.get("gpu", False))
        self.model.set(model_label(name))
        if not info.get("gpu"):
            self.write_log(
                "Видеокарта NVIDIA не найдена: расшифровка пойдёт на процессоре "
                "и будет идти долго. Выбрана самая лёгкая модель.", "dim")
        self.sync_requirements()

    def save_settings(self) -> None:
        data = {
            "out_dir": self.out_dir.get().strip(),
            "language": self.language.get(),
            "model": self.model.get(),
            "speakers": self.speakers.get(),
            "diarize": self.diarize.get(),
            "wizard_shown": self.wizard_shown,
            "prompt": self.prompt.get().strip(),
        }
        try:
            SETTINGS_FILE.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass  # настройки — удобство, а не повод ломать запуск

    def sync_speakers(self) -> None:
        self.speakers_box.configure(state="readonly" if self.diarize.get() else "disabled")
        self.sync_requirements()

    def sync_token_state(self) -> None:
        """Без токена разделение говорящих невозможно — не даём его включить."""
        ready = token_is_ready()
        if not ready:
            self.diarize.set(False)
        self.diarize_box.configure(state="normal" if ready else "disabled")
        self.wizard_button.configure(
            text="Доступ к модели…" if ready else "Настроить доступ…")
        self.sync_speakers()

    def sync_requirements(self) -> None:
        """Строка требований под списком моделей."""
        name = value_of(MODELS, self.model.get())
        self.requirements.configure(
            text=requirements_text(name, self.diarize.get()) if name else "")

    def on_close(self) -> None:
        if self.process:
            if not messagebox.askokcancel("Работа не закончена",
                                          "Расшифровка ещё идёт. Прервать и выйти?"):
                return
            self.stopping = True
            self.process.terminate()
        self.save_settings()
        self.save_glossary()
        self.root.destroy()


def main() -> int:
    root = ROOT_CLASS()
    try:
        root.call("ttk::style", "theme", "use", "vista")
    except tk.TclError:
        pass
    App(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    # pythonw.exe не показывает консоль, поэтому падение при запуске иначе
    # осталось бы молчаливым: кладём разбор в файл и в окно с ошибкой.
    try:
        sys.exit(main())
    except Exception:
        import traceback

        details = traceback.format_exc()
        try:
            (PROJECT_DIR / "gui_error.log").write_text(details, encoding="utf-8")
        except OSError:
            pass
        try:
            messagebox.showerror("Транскрибатор не запустился", details[-1500:])
        except Exception:
            pass
        raise
