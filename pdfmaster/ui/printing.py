"""Печать: системный диалог (принтеры подтягиваются сами), листы со всеми правками.

Документ собирается тем же кодом, что и при сохранении (во временный файл), и
каждый лист рисуется в разрешении принтера, вписанным в поле печати. Альбомные
листы на книжной бумаге автоматически поворачиваются.
"""
from __future__ import annotations

import os
import tempfile

import pymupdf as fitz
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtPrintSupport import QPrintDialog, QPrinter
from PySide6.QtWidgets import QApplication, QMessageBox, QProgressDialog


def print_document(tab) -> None:
    if tab.view.editing is not None:
        tab.view.finish_edit(True)
    n = len(tab.model.pages)
    if not n:
        return
    printer = QPrinter(QPrinter.HighResolution)
    printer.setDocName(tab.model.name)
    dlg = QPrintDialog(printer, tab)
    dlg.setWindowTitle("Печать")
    dlg.setMinMax(1, n)
    dlg.setFromTo(1, n)
    dlg.setOption(QPrintDialog.PrintPageRange, True)
    dlg.setOption(QPrintDialog.PrintCurrentPage, True)
    if not dlg.exec():
        return
    rng = printer.printRange()
    if rng == QPrinter.PageRange and printer.fromPage() > 0:
        idx = list(range(printer.fromPage() - 1, min(n, printer.toPage())))
    elif rng == QPrinter.CurrentPage:
        idx = [tab.view.current_page()]
    else:
        idx = list(range(n))
    pids = [tab.model.pages[i]["id"] for i in idx]
    fd, tmp = tempfile.mkstemp(prefix="pdfm-print-", suffix=".pdf")
    os.close(fd)
    prog = QProgressDialog("Готовлю листы к печати…", "Отмена", 0, len(pids) + 1, tab)
    prog.setWindowTitle("Печать")
    prog.setWindowModality(Qt.WindowModal)
    prog.setMinimumDuration(300)

    def built(res):
        try:
            _paint(printer, tmp, prog)
        finally:
            prog.close()
            try:
                os.unlink(tmp)
            except OSError:
                pass

    def failed(msg):
        prog.close()
        QMessageBox.critical(tab, "Печать", f"Не получилось подготовить документ:\n{msg}")

    sids = sorted({r["src"] for r in tab.model.pages} | {tab.model.main})
    tab.service.submit("save", (tab.model.job_state(), tmp, {}, pids), sids=sids, aids=list(tab.model.assets),
                       priority=-100, callback=built, errback=failed)


def _paint(printer: QPrinter, path: str, prog: QProgressDialog) -> None:
    doc = fitz.open(path)
    dpi = min(printer.resolution(), 300)
    k = printer.resolution() / dpi
    p = QPainter()
    if not p.begin(printer):
        QMessageBox.warning(None, "Печать", "Принтер недоступен.")
        return
    try:
        for i, page in enumerate(doc):
            if prog.wasCanceled():
                break
            prog.setValue(i + 1)
            QApplication.processEvents()
            if i:
                printer.newPage()
            area = printer.pageLayout().paintRectPixels(printer.resolution())
            aw, ah = area.width(), area.height()
            pw, ph = page.rect.width, page.rect.height
            rot = 90 if (pw > ph) != (aw > ah) else 0  # альбомный лист на книжной бумаге — повернуть
            if rot:
                pw, ph = ph, pw
            s = min(aw / pw, ah / ph)                   # пикселей принтера на пункт
            scale = s / k                               # рендерим не больше 300 dpi
            pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale).prerotate(rot), alpha=False)
            img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format_RGB888)
            w, h = pw * s, ph * s
            p.drawImage(QRectF((aw - w) / 2, (ah - h) / 2, w, h), img)
    finally:
        p.end()
        doc.close()
