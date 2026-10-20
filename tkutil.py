"""Мелочи для Tkinter, которых не хватает из коробки."""

from __future__ import annotations

import tkinter as tk

# Коды клавиш Windows: они не зависят от раскладки, в отличие от символов.
KEY_A, KEY_C, KEY_V, KEY_X = 65, 67, 86, 88
CONTROL_HELD = 0x4


def enable_clipboard_shortcuts(widget: tk.Misc) -> None:
    """Чинит Ctrl+C/V/X/A при русской раскладке клавиатуры.

    Стандартные привязки Tk сделаны на символы (<Control-v>), а при русской
    раскладке та же клавиша даёт «м» — и вставка молча не работает. Поэтому
    ловим нажатие по коду клавиши и сами шлём виртуальное событие.
    """

    def handle(event: tk.Event) -> str | None:
        if not event.state & CONTROL_HELD:
            return None
        target = event.widget
        if not isinstance(target, (tk.Entry, tk.Text, tk.Listbox)) and not (
                hasattr(target, "event_generate") and hasattr(target, "select_range")):
            # ttk.Entry не наследуется от tk.Entry, но событие принимает.
            if not hasattr(target, "event_generate"):
                return None

        if event.keycode == KEY_V:
            target.event_generate("<<Paste>>")
            return "break"
        if event.keycode == KEY_C:
            target.event_generate("<<Copy>>")
            return "break"
        if event.keycode == KEY_X:
            target.event_generate("<<Cut>>")
            return "break"
        if event.keycode == KEY_A:
            try:
                target.select_range(0, "end")
                target.icursor("end")
            except (AttributeError, tk.TclError):
                return None
            return "break"
        return None

    widget.bind_all("<Control-KeyPress>", handle, add="+")


def paste_into(entry: tk.Misc) -> None:
    """Вставляет содержимое буфера в поле — для кнопки «Вставить»."""
    try:
        text = entry.clipboard_get()
    except tk.TclError:
        return
    try:
        entry.delete(0, "end")
        entry.insert(0, text.strip())
    except tk.TclError:
        pass
