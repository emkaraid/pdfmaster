"""Номер версии — из pdfmaster/resources/VERSION (его же читают сборка и установщики)."""
import os

from .core.fonts import resource_dir


def version() -> str:
    try:
        with open(os.path.join(resource_dir(), "VERSION"), encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return "0.0.0"


def parse(v: str) -> tuple:
    out = []
    for part in v.strip().lstrip("vV").split("."):
        num = "".join(ch for ch in part if ch.isdigit())
        out.append(int(num) if num else 0)
    return tuple(out + [0] * (3 - len(out)))
