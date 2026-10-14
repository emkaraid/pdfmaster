"""Шрифты для редактора на месте: тот же файл, что попадёт в PDF."""
from __future__ import annotations

from PySide6.QtGui import QFontDatabase, QGuiApplication

_families: dict[str, str] = {}


def family_for_file(path: str | None) -> str:
    if not path:
        return "Times New Roman"
    fam = _families.get(path)
    if fam is None:
        fid = QFontDatabase.addApplicationFont(path)
        fams = QFontDatabase.applicationFontFamilies(fid) if fid >= 0 else []
        fam = fams[0] if fams else "Times New Roman"
        _families[path] = fam
    return fam


def pt_factor() -> float:
    """Сколько «пунктов Qt» в одном пункте PDF на этом экране (на macOS — 1)."""
    scr = QGuiApplication.primaryScreen()
    dpi = scr.logicalDotsPerInchY() if scr else 72.0
    return 72.0 / dpi if dpi else 1.0
