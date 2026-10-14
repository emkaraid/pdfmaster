"""Сценарий 3: лист с /Rotate 90 (и 270) — печать и текст встают ровно, не перевёрнуты."""
import io

import numpy as np
import pymupdf as fitz
import pytest
from PIL import Image

from pdfmaster.core import saver
from pdfmaster.core.model import DocModel


def _stamp_png() -> bytes:
    """200x100: верх красный, низ синий, прозрачный фон по краям."""
    im = Image.new("RGBA", (200, 100), (0, 0, 0, 0))
    a = np.array(im)
    a[5:50, 5:195] = (230, 20, 20, 255)
    a[50:95, 5:195] = (20, 20, 230, 255)
    b = io.BytesIO()
    Image.fromarray(a).save(b, "PNG")
    return b.getvalue()


def _centroid(img, col):
    m = (np.abs(img[..., 0] - col[0]) < 50) & (np.abs(img[..., 1] - col[1]) < 50) & (np.abs(img[..., 2] - col[2]) < 50)
    ys, xs = np.nonzero(m)
    assert len(xs) > 50, f"цвет {col} не найден"
    return xs.mean(), ys.mean()


@pytest.mark.parametrize("pno", [1, 2])
def test_stamp_and_text_upright_on_rotated_page(fx, tmp_path, pno):
    m = DocModel.open(fx["rotated"])
    ref = m.pages[pno]
    assert m.total_rotation(ref) in (90, 270)
    dw, dh = m.display_size(ref)
    assert dw > dh  # книжный лист с поворотом виден альбомным
    aid = m.add_asset(_stamp_png())
    # печать: центр в (300, 250) на экране, 120x60 пунктов
    cx, cy = m.to_unrotated(ref, 300, 250)
    m.add_obj(ref["id"], {"t": "image", "asset": aid, "cx": cx, "cy": cy, "w": 120, "h": 60,
                          "angle": m.upright_angle(ref)})
    # текст: левый верхний угол в (100, 400) на экране
    tx, ty = m.to_unrotated(ref, 100, 400)
    m.add_obj(ref["id"], {"t": "text", "x": tx, "y": ty, "angle": m.upright_angle(ref), "text": "Проверено",
                          "family": "sans", "size": 14, "color": "#000000"})
    out = tmp_path / "o.pdf"
    saver.save(m.sources, m.job_state(), m.assets, str(out))
    d = fitz.open(out)
    page = d[pno]
    assert page.rotation == m.total_rotation(ref)
    pix = page.get_pixmap(dpi=72, alpha=False)  # рендер с учётом /Rotate — как увидит человек
    img = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, 3).astype(int)
    rx, ry = _centroid(img, (230, 20, 20))
    bx, by = _centroid(img, (20, 20, 230))
    assert ry < by - 15, "печать перевёрнута или лежит на боку"
    assert abs(rx - bx) < 3
    assert abs((rx + bx) / 2 - 300) < 3 and abs((ry + by) / 2 - 250) < 3
    # текст читается слева направо на повёрнутом листе
    lines = [l for b in page.get_text("dict")["blocks"] for l in b.get("lines", [])
             if "Проверено" in "".join(s["text"] for s in l["spans"])]
    assert lines
    dx, dy = lines[0]["dir"]
    rm = page.rotation_matrix
    vx, vy = dx * rm.a + dy * rm.c, dx * rm.b + dy * rm.d
    assert abs(vx - 1) < 1e-3 and abs(vy) < 1e-3
    r = fitz.Rect(lines[0]["bbox"]) * rm
    assert abs(r.x0 - 100) < 2 and abs(r.y0 - 400) < 4


def test_edit_text_on_rotated_page(fx, tmp_path):
    from pdfmaster.core.textlines import extract_lines
    m = DocModel.open(fx["rotated"])
    ref = m.pages[1]
    ln = [l for l in extract_lines(m.src_page(ref)) if "Монтаж" in l["text"]][0]
    m.add_obj(ref["id"], {"t": "edit", "lines": [ln], "text": ln["text"].replace("Монтаж", "Стройка"), "style": None})
    out = tmp_path / "o.pdf"
    saver.save(m.sources, m.job_state(), m.assets, str(out))
    t = fitz.open(out)[1].get_text()
    assert "Стройка" in t and "Монтаж" not in t
