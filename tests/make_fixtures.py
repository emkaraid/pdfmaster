"""Генератор тестовых файлов. Запуск: python tests/make_fixtures.py [папка]

act_word.pdf      — акт как из Word: Times New Roman (Type0/Identity-H, подмножество),
                    кириллица, таблица с рамками, сумма 1 250 000 в тексте и в таблице.
act_lo.pdf        — акт из LibreOffice (простые TrueType-подмножества) — из прототипа.
drawing_a3.pdf    — чертёж A3: 32 000 линий, штамп ГОСТ-шрифтом, вертикальная надпись.
rotated.pdf       — листы с /Rotate 90 и 270 и обычный.
pages6.pdf        — 6 листов «Лист N» для операций с листами.
protected.pdf     — защищён от изменений (пароль владельца, без пароля на открытие).
stamp_scan.jpg    — скан синей печати на сероватом неравномерном фоне с шумом.
"""
from __future__ import annotations

import math
import os
import random
import shutil
import sys

import numpy as np
import pymupdf as fitz
from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FONTS = os.path.join(ROOT, "pdfmaster", "resources", "fonts")
SYS = "/System/Library/Fonts/Supplemental"


def _times() -> str:
    p = os.path.join(SYS, "Times New Roman.ttf")
    return p if os.path.exists(p) else os.path.join(FONTS, "LiberationSerif-Regular.ttf")


def _times_bold() -> str:
    p = os.path.join(SYS, "Times New Roman Bold.ttf")
    return p if os.path.exists(p) else os.path.join(FONTS, "LiberationSerif-Bold.ttf")


ACT_HTML = """
<html><body style="font-family:'{fam}'; font-size:12pt">
<p align="right">Директору ООО «Урожай»<br>Иванову И. И.</p>
<p align="center" style="font-size:14pt"><b>АКТ № 15</b><br><b>о приёмке выполненных работ</b></p>
<p>г. Красноярск &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; «07» октября 2026 г.</p>
<p align="justify">Мы, нижеподписавшиеся, представитель Заказчика и представитель Подрядчика, составили
настоящий акт о том, что работы по объекту «Насосная станция II подъёма» выполнены в полном объёме,
в установленные сроки и с надлежащим качеством. Претензий по объёму и срокам выполнения работ стороны не имеют.</p>
<table border="1" cellspacing="0" cellpadding="4" width="100%">
<tr><th>№</th><th>Наименование работ</th><th>Ед.</th><th>Кол-во</th><th>Сумма, руб.</th></tr>
<tr><td>1</td><td>Монтаж трубопровода Ду 300</td><td>м</td><td>120</td><td align="right">870 000</td></tr>
<tr><td>2</td><td>Установка запорной арматуры</td><td>шт.</td><td>6</td><td align="right">380 000</td></tr>
<tr><td></td><td><b>Итого</b></td><td></td><td></td><td align="right"><b>1 250 000</b></td></tr>
</table>
<p>Общая стоимость выполненных работ составляет 1 250 000 руб. 00 коп., НДС не облагается.</p>
<p>Заказчик: ____________ / Иванов И. И. /</p>
<p>Подрядчик: ____________ / Хабибулин И. Р. /</p>
</body></html>
"""


def make_act_word(path: str) -> None:
    """PDF через движок Qt: шрифты Type0 Identity-H с ToUnicode, как у Word."""
    from PySide6.QtCore import QMarginsF, QSizeF
    from PySide6.QtGui import (QFont, QFontDatabase, QPageLayout, QPageSize,
                               QPdfWriter, QTextDocument)
    from PySide6.QtWidgets import QApplication
    # QApplication (а не QGuiApplication): тот же экземпляр потом нужен тестам с окнами
    app = QApplication.instance() or QApplication(["x"])
    fid = QFontDatabase.addApplicationFont(_times())
    QFontDatabase.addApplicationFont(_times_bold())
    fam = QFontDatabase.applicationFontFamilies(fid)[0] if fid >= 0 else "Times New Roman"
    w = QPdfWriter(path)
    w.setPageSize(QPageSize(QPageSize.A4))
    w.setResolution(72)
    w.setPageMargins(QMarginsF(25, 15, 15, 15), QPageLayout.Millimeter)
    w.setCreator("Microsoft® Word (тестовый)")
    d = QTextDocument()
    d.setDefaultFont(QFont(fam, 12))
    d.setHtml(ACT_HTML.format(fam=fam))
    d.setPageSize(QSizeF(w.width(), w.height()))
    d.print_(w)
    del w


def make_drawing(path: str, n_lines: int = 32000) -> None:
    doc = fitz.open()
    W, H = fitz.paper_size("a3-l")
    page = doc.new_page(width=W, height=H)
    rnd = random.Random(7)
    sh = page.new_shape()
    # рамка по ГОСТ: 20 мм слева, 5 мм остальные
    mm = 72 / 25.4
    frame = fitz.Rect(20 * mm, 5 * mm, W - 5 * mm, H - 5 * mm)
    sh.draw_rect(frame)
    sh.finish(color=(0, 0, 0), width=1.0)
    # «трубопровод» и штриховки — много коротких линий
    for i in range(n_lines):
        x = rnd.uniform(frame.x0 + 10, frame.x1 - 200)
        y = rnd.uniform(frame.y0 + 10, frame.y1 - 120)
        a = rnd.uniform(0, math.pi)
        L = rnd.uniform(3, 18)
        sh.draw_line((x, y), (x + L * math.cos(a), y + L * math.sin(a)))
    sh.finish(color=(0.15, 0.15, 0.15), width=0.25)
    # основная надпись 185x55 мм в правом нижнем углу
    tb = fitz.Rect(frame.x1 - 185 * mm, frame.y1 - 55 * mm, frame.x1, frame.y1)
    sh.draw_rect(tb)
    for k in range(1, 5):
        y = tb.y0 + k * 11 * mm
        sh.draw_line((tb.x0, y), (tb.x1, y))
    sh.draw_line((tb.x0 + 70 * mm, tb.y0), (tb.x0 + 70 * mm, tb.y1))
    sh.draw_line((tb.x0 + 135 * mm, tb.y0), (tb.x0 + 135 * mm, tb.y1))
    sh.finish(color=(0, 0, 0), width=0.7)
    sh.commit()
    gost = os.path.join(FONTS, "osifont.ttf")
    page.insert_font(fontname="GOST", fontfile=gost)
    page.insert_text((tb.x0 + 73 * mm, tb.y0 + 8 * mm), "Насосная станция II подъёма", fontname="GOST", fontsize=12)
    page.insert_text((tb.x0 + 73 * mm, tb.y0 + 19 * mm), "Схема трубопровода", fontname="GOST", fontsize=10)
    page.insert_text((tb.x0 + 138 * mm, tb.y0 + 8 * mm), "Лист 3", fontname="GOST", fontsize=10)
    page.insert_text((tb.x0 + 3 * mm, tb.y0 + 8 * mm), "Разраб. Хабибулин", fontname="GOST", fontsize=10)
    page.insert_text((tb.x0 + 3 * mm, tb.y0 + 41 * mm), "ПК0+00 — ПК12+50", fontname="GOST", fontsize=10)
    # вертикальная надпись (читается снизу вверх)
    page.insert_text((frame.x0 + 30, frame.y1 - 160), "Отметка верха трубы 125.40", fontname="GOST",
                     fontsize=11, rotate=90)
    # тот же текст в обычной надписи на выноске
    page.insert_text((frame.x0 + 60, frame.y0 + 40), "Т1 Ø325х8", fontname="GOST", fontsize=11)
    doc.subset_fonts()
    doc.save(path, garbage=3, deflate=True)


def make_rotated(path: str) -> None:
    doc = fitz.open()
    font = os.path.join(FONTS, "PT_Sans-Web-Regular.ttf")
    for rot, title in ((0, "Лист без поворота"), (90, "Лист с поворотом 90"), (270, "Лист с поворотом 270")):
        p = doc.new_page(width=595, height=842)  # хранится «книжным»
        p.insert_font(fontname="F", fontfile=font)
        p.insert_text((60, 80), title, fontname="F", fontsize=18)
        p.insert_text((60, 120), "Подрядчик: ООО «Монтаж»", fontname="F", fontsize=12)
        p.draw_rect(fitz.Rect(40, 40, 555, 802), color=(0, 0, 0), width=1)
        p.set_rotation(rot)
    doc.subset_fonts()
    doc.save(path, garbage=3, deflate=True)


def make_pages(path: str, n: int = 6) -> None:
    doc = fitz.open()
    font = os.path.join(FONTS, "PT_Sans-Web-Regular.ttf")
    for i in range(1, n + 1):
        w, h = (842, 595) if i == 4 else (595, 842)  # 4-й лист альбомный
        p = doc.new_page(width=w, height=h)
        p.insert_font(fontname="F", fontfile=font)
        p.insert_text((60, 100), f"Лист {i}", fontname="F", fontsize=40)
    doc[2].set_rotation(90)  # у третьего листа уже есть /Rotate 90
    doc.set_toc([[1, "Начало", 1], [1, "Третий", 3]])
    doc.subset_fonts()
    doc.save(path, garbage=3, deflate=True)


def make_protected(src: str, path: str) -> None:
    doc = fitz.open(src)
    perm = fitz.PDF_PERM_PRINT | fitz.PDF_PERM_ACCESSIBILITY | fitz.PDF_PERM_COPY
    doc.save(path, encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw="owner-secret", user_pw="",
             permissions=perm)


def make_stamp_scan(path: str) -> None:
    rnd = np.random.default_rng(3)
    S = 900
    # неравномерный сероватый фон бумаги
    yy, xx = np.mgrid[0:S, 0:S].astype(np.float32)
    base = 214 + 18 * (xx / S) - 12 * (yy / S)
    bg = np.dstack([base + 4, base + 2, base - 6])
    img = Image.fromarray(np.clip(bg, 0, 255).astype(np.uint8))
    d = ImageDraw.Draw(img)
    ink = (38, 62, 158)
    c = S // 2
    d.ellipse((c - 380, c - 380, c + 380, c + 380), outline=ink, width=14)
    d.ellipse((c - 300, c - 300, c + 300, c + 300), outline=ink, width=6)
    try:
        f = ImageFont.truetype(os.path.join(FONTS, "PT_Sans-Web-Bold.ttf"), 64)
        f2 = ImageFont.truetype(os.path.join(FONTS, "PT_Sans-Web-Regular.ttf"), 44)
    except Exception:
        f = f2 = ImageFont.load_default()
    d.text((c, c - 40), "ООО «МОНТАЖ»", fill=ink, font=f, anchor="mm")
    d.text((c, c + 40), "ИНН 2465000000", fill=ink, font=f2, anchor="mm")
    d.text((c, c + 110), "г. Красноярск", fill=ink, font=f2, anchor="mm")
    img = img.filter(ImageFilter.GaussianBlur(1.2))
    a = np.asarray(img).astype(np.float32)
    a += rnd.normal(0, 4.0, a.shape)  # шум сканера
    # пыль
    for _ in range(40):
        y, x = rnd.integers(0, S, 2)
        a[y:y + 2, x:x + 2] -= 70
    Image.fromarray(np.clip(a, 0, 255).astype(np.uint8)).save(path, quality=82, dpi=(300, 300))


def make_all(out: str) -> dict:
    os.makedirs(out, exist_ok=True)
    paths = {k: os.path.join(out, v) for k, v in {
        "act": "act_word.pdf", "act_lo": "act_lo.pdf", "drawing": "drawing_a3.pdf", "rotated": "rotated.pdf",
        "pages": "pages6.pdf", "protected": "protected.pdf", "stamp": "stamp_scan.jpg"}.items()}
    if not os.path.exists(paths["act"]):
        make_act_word(paths["act"])
    lo = os.path.join(ROOT, "prototype", "test", "doc.pdf")
    if not os.path.exists(paths["act_lo"]) and os.path.exists(lo):
        shutil.copy(lo, paths["act_lo"])
    if not os.path.exists(paths["drawing"]):
        make_drawing(paths["drawing"])
    if not os.path.exists(paths["rotated"]):
        make_rotated(paths["rotated"])
    if not os.path.exists(paths["pages"]):
        make_pages(paths["pages"])
    if not os.path.exists(paths["protected"]):
        make_protected(paths["act"], paths["protected"])
    if not os.path.exists(paths["stamp"]):
        make_stamp_scan(paths["stamp"])
    return paths


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "fixtures")
    for k, v in make_all(out).items():
        print(k, v, os.path.getsize(v) if os.path.exists(v) else "—")
