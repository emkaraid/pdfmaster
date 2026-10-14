"""Сценарий 2: тяжёлый чертёж A3 — открытие, рендер, правка надписи, печать."""
import time

import pymupdf as fitz

from pdfmaster.core import images, saver
from pdfmaster.core.bake import BakeContext, bake_page
from pdfmaster.core.model import DocModel
from pdfmaster.core.textlines import extract_lines
from tests.conftest import pdftotext

TIMES = {}


def test_drawing_has_30k_lines(fx):
    p = fitz.open(fx["drawing"])[0]
    n = sum(len(x["items"]) for x in p.get_cdrawings())
    assert n >= 30000
    assert abs(p.rect.width - 1190.55) < 1 and abs(p.rect.height - 841.89) < 1


def test_render_speed(fx):
    t0 = time.perf_counter()
    m = DocModel.open(fx["drawing"])
    TIMES["open"] = time.perf_counter() - t0
    page = m.src_page(m.pages[0])
    t0 = time.perf_counter()
    dl = page.get_displaylist()
    TIMES["displaylist"] = time.perf_counter() - t0
    for z in (0.5, 1.0, 2.0):
        t0 = time.perf_counter()
        dl.get_pixmap(matrix=fitz.Matrix(z, z), alpha=False)
        TIMES[f"render_{z}x"] = time.perf_counter() - t0
    # фрагмент при сильном увеличении (как плитка экрана)
    t0 = time.perf_counter()
    dl.get_pixmap(matrix=fitz.Matrix(8, 8), clip=fitz.Rect(400, 300, 600, 450), alpha=False)
    TIMES["tile_8x"] = time.perf_counter() - t0
    print("\nВремя (с):", {k: round(v, 3) for k, v in TIMES.items()})
    assert TIMES["open"] < 1.0
    assert TIMES["render_1.0x"] < 1.5
    assert TIMES["render_2.0x"] < 3.0
    assert TIMES["tile_8x"] < 1.0


def test_preview_bake_speed_and_edit(fx, tmp_path):
    m = DocModel.open(fx["drawing"])
    ref = m.pages[0]
    t0 = time.perf_counter()
    lines = extract_lines(m.src_page(ref))
    t_lines = time.perf_counter() - t0
    ln = [l for l in lines if "125.40" in l["text"]][0]
    m.add_obj(ref["id"], {"t": "edit", "lines": [ln], "text": ln["text"].replace("125.40", "126.10"), "style": None})
    png = images.to_png(images.remove_background(images.open_image(open(fx["stamp"], "rb").read())))
    aid = m.add_asset(png)
    w, h = m.display_size(ref)
    m.add_obj(ref["id"], {"t": "image", "asset": aid, "cx": w - 300, "cy": h - 120, "w": 113, "h": 113, "angle": 0})
    # предпросмотр: копия листа + запекание + рендер — так делает процесс отрисовки
    t0 = time.perf_counter()
    tmp = fitz.open()
    tmp.insert_pdf(m.sources[ref["src"]].doc, from_page=0, to_page=0)
    bake_page(tmp[0], m.page_objs(ref["id"]), BakeContext(assets=m.assets))
    tmp[0].get_pixmap(matrix=fitz.Matrix(1, 1), alpha=False)
    t_preview = time.perf_counter() - t0
    print(f"\nстроки текста: {t_lines:.3f} с, предпросмотр с правкой: {t_preview:.3f} с")
    assert t_lines < 1.0 and t_preview < 2.0
    out = tmp_path / "o.pdf"
    t0 = time.perf_counter()
    saver.save(m.sources, m.job_state(), m.assets, str(out))
    print(f"сохранение: {time.perf_counter() - t0:.3f} с")
    txt = pdftotext(str(out))
    assert "126.10" in txt and "125.40" not in txt
    d = fitz.open(out)
    assert len(d[0].get_images()) == 1
    assert sum(len(x["items"]) for x in d[0].get_cdrawings()) >= 30000
