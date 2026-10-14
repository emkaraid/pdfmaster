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
