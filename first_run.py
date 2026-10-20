"""Мастер первого запуска: получение токена Hugging Face.

Модель разделения говорящих закрытая — её скачивают только авторизованным
пользователям, принявшим условия. Токен привязан к аккаунту и не передаётся
между людьми, поэтому каждый, кому передали программу, проходит эту настройку
сам. Занимает пару минут и делается один раз.
"""

from __future__ import annotations

import queue
import threading
import tkinter as tk
import webbrowser
from tkinter import ttk

from tkutil import enable_clipboard_shortcuts, paste_into
from transcribe import DIARIZATION_MODEL, PROJECT_DIR, resolve_hf_token

TOKEN_FILE = PROJECT_DIR / "hf_token.txt"

JOIN_URL = "https://huggingface.co/join"
MODEL_URL = f"https://hf.co/{DIARIZATION_MODEL}"
TOKENS_URL = "https://hf.co/settings/tokens"

INTRO = (
    "Чтобы в расшифровке появлялись подписи «Спикер 1», «Спикер 2», нужен "
    "бесплатный доступ к модели разделения говорящих. Настройка разовая, "
    "занимает пару минут.\n\n"
    "Без неё программа работает: получится сплошной текст с таймкодами."
)


class _Args:
    """resolve_hf_token ждёт объект с полем hf_token."""

    hf_token = None


def token_is_ready() -> bool:
    """Есть ли уже рабочий токен (проверяется только наличие, без сети)."""
    return bool(resolve_hf_token(_Args()))


def check_access(token: str) -> tuple[bool, str]:
    """Проверяет токен и доступ к модели. Возвращает (успех, сообщение)."""
    from huggingface_hub import HfApi
    from huggingface_hub.errors import GatedRepoError, RepositoryNotFoundError

    api = HfApi()
    try:
        who = api.whoami(token=token)
    except Exception:
        return False, ("Токен не принят. Проверьте, что скопировали строку "
                       "целиком, вместе с началом «hf_».")

    name = who.get("name", "")
    try:
        api.model_info(DIARIZATION_MODEL, token=token)
    except (GatedRepoError, RepositoryNotFoundError):
        return False, (f"Токен рабочий (аккаунт {name}), но доступ к модели не выдан.\n"
                       "Откройте страницу модели кнопкой «шаг 2» и нажмите там "
                       "«Agree and access repository».")
    except Exception as error:
        return False, f"Не удалось проверить доступ: {error}"

    return True, f"Готово. Аккаунт {name}, доступ к модели есть."


def save_token(token: str) -> None:
    TOKEN_FILE.write_text(token.strip() + "\n", encoding="utf-8")


class Wizard(tk.Toplevel):
    """Модальное окно настройки. Результат — в self.enabled."""

    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(parent)
        self.title("Настройка разделения говорящих")
        self.resizable(False, False)
        self.enabled = False
        self.results: queue.Queue = queue.Queue()

        frame = ttk.Frame(self, padding=16)
        frame.grid(sticky="nsew")
        frame.columnconfigure(0, weight=1)

        ttk.Label(frame, text=INTRO, wraplength=520, justify="left").grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 14))

        steps = ttk.LabelFrame(frame, text="Три шага на сайте Hugging Face", padding=10)
        steps.grid(row=1, column=0, columnspan=2, sticky="ew")
        steps.columnconfigure(1, weight=1)

        self._step(steps, 0, "Шаг 1", "Завести бесплатный аккаунт (или войти в свой)",
                   "Открыть регистрацию", JOIN_URL)
        self._step(steps, 1, "Шаг 2", "Принять условия модели: кнопка "
                                      "«Agree and access repository»",
                   "Открыть страницу модели", MODEL_URL)
        self._step(steps, 2, "Шаг 3", "Создать токен типа Read и скопировать его",
                   "Открыть страницу токенов", TOKENS_URL)

        ttk.Label(frame, text="Вставьте токен сюда (Ctrl+V или кнопка «Вставить»):"
                  ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(14, 2))

        row = ttk.Frame(frame)
        row.grid(row=3, column=0, columnspan=2, sticky="ew")
        row.columnconfigure(0, weight=1)
        self.token = tk.StringVar()
        self.entry = ttk.Entry(row, textvariable=self.token)
        self.entry.grid(row=0, column=0, sticky="ew")
        self.entry.focus_set()
        ttk.Button(row, text="Вставить", width=11,
                   command=lambda: paste_into(self.entry)).grid(
            row=0, column=1, padx=(6, 0))
        self.check_button = ttk.Button(row, text="Проверить и сохранить",
                                       command=self.verify)
        self.check_button.grid(row=0, column=2, padx=(6, 0))

        # Ctrl+V в русской раскладке без этого не работает.
        enable_clipboard_shortcuts(self)

        self.status = ttk.Label(frame, text="", wraplength=520, justify="left",
                                foreground="#6b7280")
        self.status.grid(row=4, column=0, columnspan=2, sticky="w", pady=(10, 0))

        bottom = ttk.Frame(frame)
        bottom.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(16, 0))
        bottom.columnconfigure(0, weight=1)
        ttk.Button(bottom, text="Пропустить — работать без разделения говорящих",
                   command=self.skip).grid(row=0, column=0, sticky="w")

        self.bind("<Return>", lambda _event: self.verify())
        self.protocol("WM_DELETE_WINDOW", self.skip)
        self.transient(parent)
        self.grab_set()
        self.after(100, self._drain)

    def _step(self, parent: ttk.Frame, row: int, number: str, text: str,
              button: str, url: str) -> None:
        ttk.Label(parent, text=number, font=("Segoe UI", 9, "bold")).grid(
            row=row, column=0, sticky="nw", pady=3)
        ttk.Label(parent, text=text, wraplength=300, justify="left").grid(
            row=row, column=1, sticky="w", padx=8, pady=3)
        ttk.Button(parent, text=button,
                   command=lambda u=url: webbrowser.open(u)).grid(
            row=row, column=2, sticky="ew", pady=3)

    def verify(self) -> None:
        token = self.token.get().strip()
        if not token:
            self.status.configure(text="Сначала вставьте токен.", foreground="#b42318")
            return
        self.check_button.configure(state="disabled")
        self.status.configure(text="Проверяю…", foreground="#6b7280")
        threading.Thread(target=lambda: self.results.put(check_access(token)),
                         daemon=True).start()

    def _drain(self) -> None:
        try:
            ok, message = self.results.get_nowait()
        except queue.Empty:
            self.after(100, self._drain)
            return

        self.check_button.configure(state="normal")
        self.status.configure(text=message,
                              foreground="#1a7f37" if ok else "#b42318")
        if ok:
            save_token(self.token.get().strip())
            self.enabled = True
            self.after(1200, self.destroy)
        else:
            self.after(100, self._drain)

    def skip(self) -> None:
        self.enabled = False
        self.destroy()


def run_wizard(parent: tk.Misc) -> bool:
    """Показывает мастер и ждёт его закрытия. True — токен сохранён."""
    wizard = Wizard(parent)
    parent.wait_window(wizard)
    return wizard.enabled
