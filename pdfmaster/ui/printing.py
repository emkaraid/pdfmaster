"""Печать: системный диалог (принтеры подтягиваются сами), листы со всеми правками.

Документ собирается тем же кодом, что и при сохранении (во временный файл).

Mac: листы вписываются в формат бумаги (вектором, как есть) и уходят на принтер
через системную очередь печати (lp), как при печати из «Просмотра». Картинками
листы не отправляем: такой файл огромен, и принтеры (например Kyocera) отвечают
листом «VMerror / Not enough memory».

Windows: каждый лист рисуется в разрешении принтера через драйвер. Альбомные
листы на книжной бумаге в обоих случаях поворачиваются.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile

import pymupdf as fitz
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QImage, QPageSize, QPainter
from PySide6.QtPrintSupport import QPrintDialog, QPrinter
from PySide6.QtWidgets import QApplication, QMessageBox, QProgressDialog

IS_WIN = sys.platform.startswith("win")


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
    to_file = printer.outputFormat() == QPrinter.PdfFormat and printer.outputFileName()
    use_lp = not to_file and not IS_WIN and bool(printer.printerName()) and shutil.which("lp")
    fd, tmp = tempfile.mkstemp(prefix="pdfm-print-", suffix=".pdf")
    os.close(fd)
    prog = QProgressDialog("Готовлю листы к печати…", "Отмена", 0, len(pids) + 1, tab)
    prog.setWindowTitle("Печать")
    prog.setWindowModality(Qt.WindowModal)
    prog.setMinimumDuration(300)

    def built(res):
        try:
            if to_file:   # «Сохранить как PDF» в системном окне печати
                shutil.copyfile(tmp, printer.outputFileName())
            elif use_lp:
                prog.setLabelText("Отправляю на принтер…")
                QApplication.processEvents()
                send_to_printer(printer, tmp, tab.model.name)
                tab.toast(f"Отправлено на печать: {printer.printerName().replace('_', ' ')}")
            else:
                _paint(printer, tmp, prog)
        except Exception as e:
            QMessageBox.critical(tab, "Печать", f"Не получилось напечатать:\n{e}")
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


# ------------------------------------------------------------ Mac: вектором через очередь печати

def paper_of(printer: QPrinter) -> tuple[float, float, float, str]:
    """Бумага из окна печати: ширина и высота (книжная, в пунктах), поле, имя формата для lp."""
    lay = printer.pageLayout()
    s = lay.pageSize().size(QPageSize.Point)
    w, h = sorted((s.width(), s.height()))
    m = lay.marginsPoints()
    margin = min(max(m.left(), m.right(), m.top(), m.bottom(), 12.0), 36.0)
    key = lay.pageSize().key()
    return w, h, margin, "" if key.lower().startswith("custom") else key


def fit_to_paper(src_path: str, dst_path: str, w: float, h: float, margin: float = 12.0) -> int:
    """Каждый лист — на отдельный лист бумаги w×h (книжный): альбомные поворачиваются,
    большие (чертежи A1–A3) уменьшаются до поля печати, лист размера бумаги идёт 1:1.
    Содержимое переносится вектором; картинки с избыточным разрешением ужимаются до 300 dpi."""
    src = fitz.open(src_path)
    out = fitz.open()
    try:
        for p in src:
            r = p.rect                          # видимый размер, с учётом /Rotate
            rot = 90 if r.width > r.height else 0
            vw, vh = (r.height, r.width) if rot else (r.width, r.height)
            page = out.new_page(width=w, height=h)
            if abs(vw - w) <= 0.03 * w and abs(vh - h) <= 0.03 * h:
                target = page.rect               # формат бумаги — в натуральную величину
            else:
                s = min((w - 2 * margin) / vw, (h - 2 * margin) / vh, 1.0)  # только уменьшаем
                tw, th = vw * s, vh * s
                target = fitz.Rect((w - tw) / 2, (h - th) / 2, (w + tw) / 2, (h + th) / 2)
            page.show_pdf_page(target, src, p.number, rotate=rot)
        try:
            out.rewrite_images(dpi_threshold=400, dpi_target=300, quality=85, lossy=True, lossless=True,
                               bitonal=False, color=True, gray=True)
        except Exception:
            pass
        out.save(dst_path, garbage=3, deflate=True)
        return out.page_count
    finally:
        out.close()
        src.close()


def lp_command(printer: QPrinter, path: str, title: str, media: str = "") -> list[str]:
    cmd = ["lp", "-d", printer.printerName(), "-t", title or "PDFMaster"]
    copies = max(1, printer.copyCount())
    if copies > 1:
        cmd += ["-n", str(copies), "-o", f"collate={'true' if printer.collateCopies() else 'false'}"]
    sides = {QPrinter.DuplexLongSide: "two-sided-long-edge", QPrinter.DuplexShortSide: "two-sided-short-edge",
             QPrinter.DuplexNone: "one-sided"}.get(printer.duplex())
    if sides:
        cmd += ["-o", f"sides={sides}"]
    if printer.colorMode() == QPrinter.GrayScale:
        cmd += ["-o", "print-color-mode=monochrome"]
    if media:
        cmd += ["-o", f"media={media}"]
    return cmd + [path]


def send_to_printer(printer: QPrinter, path: str, title: str) -> None:
    w, h, margin, media = paper_of(printer)
    fd, fitted = tempfile.mkstemp(prefix="pdfm-paper-", suffix=".pdf")
    os.close(fd)
    try:
        fit_to_paper(path, fitted, w, h, margin)
        r = subprocess.run(lp_command(printer, fitted, title, media), capture_output=True, text=True, timeout=180)
        if r.returncode != 0:
            raise RuntimeError((r.stderr or r.stdout or f"lp: код {r.returncode}").strip())
    finally:
        try:
            os.unlink(fitted)
        except OSError:
            pass


# ------------------------------------------------------------ Windows: через драйвер принтера

def _paint(printer: QPrinter, path: str, prog: QProgressDialog | None = None) -> None:
    doc = fitz.open(path)
    dpi = min(printer.resolution(), 300)
    k = printer.resolution() / dpi
    gray = printer.colorMode() == QPrinter.GrayScale
    p = QPainter()
    if not p.begin(printer):
        QMessageBox.warning(None, "Печать", "Принтер недоступен.")
        return
    try:
        for i, page in enumerate(doc):
            if prog is not None:
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
            cs = fitz.csGRAY if gray else fitz.csRGB
            pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale).prerotate(rot), colorspace=cs, alpha=False)
            fmt = QImage.Format_Grayscale8 if gray else QImage.Format_RGB888
            img = QImage(pix.samples, pix.width, pix.height, pix.stride, fmt)
            w, h = pw * s, ph * s
            p.drawImage(QRectF((aw - w) / 2, (ah - h) / 2, w, h), img)
    finally:
        p.end()
        doc.close()
