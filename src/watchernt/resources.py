from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtGui import QIcon


def application_icon() -> QIcon:
    if getattr(sys, "frozen", False):
        icon_path = Path(vars(sys)["_MEIPASS"]) / "icon.png"
    else:
        icon_path = Path(__file__).resolve().parents[2] / "icon.png"
    return QIcon(str(icon_path))
