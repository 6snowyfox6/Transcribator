"""Сборка установщика.

    .venv\\Scripts\\python.exe installer\\build.py

Отдельный скрипт нужен не ради удобства: Windows PowerShell 5.1 читает .ps1
без BOM в системной кодировке, и кириллица внутри ломает разбор скрипта —
установка падает молча. Здесь BOM проставляется принудительно перед каждой
сборкой, чтобы эта ошибка не вернулась.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

INSTALLER_DIR = Path(__file__).resolve().parent
PROJECT_DIR = INSTALLER_DIR.parent
SCRIPT = INSTALLER_DIR / "Transcribator.iss"

BOM = b"\xef\xbb\xbf"

ISCC_CANDIDATES = [
    Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Inno Setup 6" / "ISCC.exe",
    Path(r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"),
    Path(r"C:\Program Files\Inno Setup 6\ISCC.exe"),
]


def ensure_bom(path: Path) -> bool:
    """Дописывает UTF-8 BOM, если его нет. True — файл был исправлен."""
    data = path.read_bytes()
    if data.startswith(BOM):
        return False
    path.write_bytes(BOM + data)
    return True


def find_iscc() -> Path:
    for candidate in ISCC_CANDIDATES:
        if candidate.is_file():
            return candidate
    raise SystemExit(
        "Не найден компилятор Inno Setup (ISCC.exe).\n"
        "Установите его: winget install --id JRSoftware.InnoSetup -e"
    )


def check_bat_files() -> list[str]:
    """.bat обязаны быть в чистом ASCII.

    cmd.exe читает пакетный файл в кодировке OEM и разбирает его построчно,
    поэтому кириллица внутри — даже в комментариях — превращается в мусорные
    команды. `chcp 65001` в начале файла не спасает.
    """
    broken = []
    for bat in sorted(PROJECT_DIR.glob("*.bat")):
        data = bat.read_bytes()
        if any(byte > 127 for byte in data):
            broken.append(bat.name)
    return broken


def main() -> int:
    for script in sorted(INSTALLER_DIR.glob("*.ps1")):
        if ensure_bom(script):
            print(f"добавлен BOM: {script.name}")

    broken = check_bat_files()
    if broken:
        raise SystemExit(
            "В этих .bat есть не-ASCII символы, cmd.exe их не разберёт: "
            + ", ".join(broken)
        )

    missing = [name for name in ("transcribe.py", "gui.py", "first_run.py",
                                 "models.py", "requirements.txt", "icon.ico")
               if not (PROJECT_DIR / name).is_file()]
    if missing:
        raise SystemExit("Не хватает файлов: " + ", ".join(missing))

    iscc = find_iscc()
    print(f"компилятор: {iscc}")
    result = subprocess.run([str(iscc), str(SCRIPT)], cwd=str(PROJECT_DIR),
                            capture_output=True, text=True, encoding="utf-8",
                            errors="replace")
    if result.returncode != 0:
        print(result.stdout[-3000:])
        print(result.stderr[-2000:], file=sys.stderr)
        raise SystemExit(f"Сборка не удалась (код {result.returncode})")

    setup = PROJECT_DIR / "dist" / "TranscribatorSetup.exe"
    size = setup.stat().st_size / 1024 / 1024
    print(f"готово: {setup} ({size:.1f} МБ)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
