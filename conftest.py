"""Гарантирует, что тесты импортируют пакет из ``src``.

В окружении может стоять устаревшая не-editable копия пакета в
site-packages, которая перекрывает актуальный исходный код из ``src``.
Добавляем ``src`` в начало ``sys.path`` до импорта пакета тестами,
чтобы pytest проверял именно рабочую копию кода.
"""

from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent / "src"

if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
