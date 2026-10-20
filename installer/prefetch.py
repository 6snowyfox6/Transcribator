"""Заранее скачивает модель распознавания речи.

Вызывается установщиком, чтобы первая расшифровка не начиналась с ожидания
загрузки в несколько гигабайт. Модель выбирается по видеопамяти машины.
"""

import json
import sys
from pathlib import Path

app_dir = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent)
sys.path.insert(0, str(app_dir))

from models import recommend  # noqa: E402

machine_file = app_dir / "machine.json"
# utf-8-sig: machine.json пишет PowerShell, а он всегда ставит BOM.
machine = (json.loads(machine_file.read_text(encoding="utf-8-sig"))
           if machine_file.is_file() else {})
name = recommend(machine.get("vram_mb", 0), machine.get("gpu", False))

print(f"выбрана модель {name}")

from faster_whisper.utils import download_model  # noqa: E402

path = download_model(name)
print(f"загружена в {path}")
