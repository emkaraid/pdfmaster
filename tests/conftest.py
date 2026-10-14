import os
import shutil
import subprocess
import sys
import tempfile

import numpy as np
import pymupdf as fitz
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# настройки (недавние файлы, папки) — во временный файл, а не в настройки программы на этом Mac
os.environ.setdefault("PDFM_SETTINGS", os.path.join(tempfile.mkdtemp(prefix="pdfm-settings-"), "settings.ini"))

from tests.make_fixtures import make_all  # noqa: E402

FIX = os.path.join(ROOT, "tests", "fixtures")


@pytest.fixture(scope="session")
def fx():
    return make_all(FIX)


@pytest.fixture(autouse=True)
def _support_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("PDFM_SUPPORT_DIR", str(tmp_path / "support"))


def pdftotext(path: str, layout: bool = False) -> str:
    exe = shutil.which("pdftotext") or "/opt/homebrew/bin/pdftotext"
    args = [exe] + (["-layout"] if layout else []) + [path, "-"]
    return subprocess.run(args, capture_output=True, text=True, check=True).stdout


def render(path_or_doc, pno: int = 0, dpi: int = 100) -> np.ndarray:
    doc = fitz.open(path_or_doc) if isinstance(path_or_doc, str) else path_or_doc
    pix = doc[pno].get_pixmap(dpi=dpi, alpha=False)
    return np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, 3).astype(np.int16)


def diff_mask(a: np.ndarray, b: np.ndarray, tol: int = 40) -> np.ndarray:
    assert a.shape == b.shape, (a.shape, b.shape)
    return np.abs(a - b).sum(axis=2) > tol


def norm_ws(s: str) -> str:
    return " ".join(s.split())
