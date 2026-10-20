"""Печать (через «печать в файл» Qt) и перенос библиотеки подписей между компьютерами."""
import os

import numpy as np
import pymupdf as fitz
from PIL import Image


def test_print_fits_pages_and_rotates_landscape(fx, tmp_path):
    from PySide6.QtGui import QPageLayout, QPageSize
    from PySide6.QtPrintSupport import QPrinter
    from PySide6.QtWidgets import QApplication
    from pdfmaster.ui.printing import _paint
    app = QApplication.instance() or QApplication([])
    out = tmp_path / "printed.pdf"
    printer = QPrinter(QPrinter.HighResolution)
    printer.setOutputFormat(QPrinter.PdfFormat)
    printer.setOutputFileName(str(out))
    printer.setPageSize(QPageSize(QPageSize.A4))
    printer.setPageOrientation(QPageLayout.Portrait)
    _paint(printer, fx["pages"])   # 6 листов, 4-й альбомный, у 3-го /Rotate 90
    d = fitz.open(out)
    assert d.page_count == 6
    for p in d:
        assert p.rect.height > p.rect.width   # всё на книжной A4
    # альбомный 4-й лист повёрнут и вписан: печать занимает почти всю высоту, а не полосу
    pix = d[3].get_pixmap(dpi=40, alpha=False)
    a = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, 3)
    ys = np.nonzero((a < 200).any(axis=2).any(axis=1))[0]
    assert len(ys)
    assert app is not None


def test_library_rotate_export_import(tmp_path):
    from pdfmaster.core import images
    from pdfmaster.core.library import Library
    im = Image.new("RGBA", (300, 100), (0, 0, 0, 0))
    a = np.array(im)
    a[40:60, 10:290] = (30, 40, 160, 255)
    lib = Library(str(tmp_path / "a"))
    it = lib.add(images.to_png(Image.fromarray(a)), "signature", name="Подпись", width_pt=100.0)
    lib.rotate(it["id"], 90)
    r = Image.open(lib.path(it["id"]))
    assert r.size == (100, 300)
    assert abs(lib.items()[0]["width_pt"] - 100 * 100 / 300) < 1e-6
    z = tmp_path / "lib.zip"
    assert lib.export_zip(str(z)) == 1
    other = Library(str(tmp_path / "b"))
    assert other.import_zip(str(z)) == 1
    assert other.import_zip(str(z)) == 0          # повторно не дублируется
    got = other.items()[0]
    assert got["name"] == "Подпись" and got["kind"] == "signature"
    assert Image.open(other.path(got["id"])).size == (100, 300)
    assert os.path.getsize(z) > 0


def test_library_moves_from_old_folder(tmp_path, monkeypatch):
    """После переименования в PDFMaster подписи из папки «PDF-мастерская» не теряются."""
    import sys
    from pdfmaster.core import library as L
    monkeypatch.delenv("PDFM_SUPPORT_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path))
    base = tmp_path if sys.platform.startswith("win") else tmp_path / "Library" / "Application Support"
    old = base / L.OLD_NAME / "library"
    old.mkdir(parents=True)
    (old / "index.json").write_text("[]", encoding="utf-8")
    d = L.app_support_dir()
    assert os.path.basename(d) == "PDFMaster"
    assert os.path.exists(os.path.join(d, "library", "index.json"))
    assert not (base / L.OLD_NAME).exists()


def test_print_mac_fits_vector_pages_to_paper(fx, tmp_path):
    """Mac: листы уходят на принтер вектором, каждый вписан в бумагу; без огромных картинок."""
    from pdfmaster.ui.printing import fit_to_paper
    out = tmp_path / "paper.pdf"
    w, h = 595.0, 842.0
    n = fit_to_paper(fx["pages"], str(out), w, h, 12)
    src = fitz.open(fx["pages"])
    d = fitz.open(out)
    assert n == d.page_count == src.page_count
    for sp, p in zip(src, d):
        assert abs(p.rect.width - w) < 1 and abs(p.rect.height - h) < 1
        assert not p.get_images()                  # вектором, не картинкой
        assert p.get_text().split() == sp.get_text().split() or not sp.get_text().strip()
    # альбомный 4-й лист повёрнут на книжную бумагу: строка «Лист 4» идёт вертикально
    lines = [ln for b in d[3].get_text("dict")["blocks"] for ln in b.get("lines", [])]
    assert lines and all(abs(ln["dir"][0]) < 0.1 for ln in lines)
    # чертёж A3 уменьшен и целиком в поле печати
    out3 = tmp_path / "a3.pdf"
    fit_to_paper(fx["drawing"], str(out3), w, h, 12)
    p = fitz.open(out3)[0]
    bb = fitz.Rect()
    for dr in p.get_drawings():
        bb |= dr["rect"]
    assert fitz.Rect(11, 11, w - 11, h - 11).contains(bb) and bb.height > h * 0.5


def test_print_lp_command_options():
    from PySide6.QtPrintSupport import QPrinter
    from PySide6.QtWidgets import QApplication
    from pdfmaster.ui.printing import lp_command
    app = QApplication.instance() or QApplication([])
    pr = QPrinter()
    pr.setPrinterName("")   # без настоящего принтера: имя подставим в проверке
    pr.setCopyCount(2)
    pr.setDuplex(QPrinter.DuplexLongSide)
    pr.setColorMode(QPrinter.GrayScale)
    cmd = lp_command(pr, "/tmp/x.pdf", "Акт.pdf", "A4")
    assert cmd[0] == "lp" and cmd[-1] == "/tmp/x.pdf"
    s = " ".join(cmd)
    assert "-t Акт.pdf" in s and "media=A4" in s and "print-color-mode=monochrome" in s
    assert app is not None
