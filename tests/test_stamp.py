"""Сценарий 5: скан печати на сероватом фоне — фон удаляется, края чистые."""
import io

import numpy as np
import pymupdf as fitz
from PIL import Image

from pdfmaster.core import images, saver
from pdfmaster.core.library import Library
from pdfmaster.core.model import DocModel


def test_background_removed(fx):
    src = images.open_image(open(fx["stamp"], "rb").read())
    out = images.remove_background(src)
    a = np.asarray(out).astype(int)
    alpha = a[..., 3]
    h, w = alpha.shape
    # обрезано по печати: поля не больше ~3% (кольцо печати почти касается краёв)
    assert w < src.width * 0.92 and h < src.height * 0.92
    assert abs(w - h) < 0.05 * w  # печать круглая
    # углы (вне круга) полностью прозрачные
    for y0, x0 in ((0, 0), (0, w - 40), (h - 40, 0), (h - 40, w - 40)):
        assert alpha[y0:y0 + 40, x0:x0 + 40].max() == 0
    # центр между кольцом и текстом (сероватый фон) — прозрачен
    cy, cx = h // 2, w // 2
    ring_gap = alpha[cy - 5:cy + 5, cx - int(0.45 * w):cx - int(0.42 * w)]
    assert ring_gap.max() < 10
    # чернила непрозрачные и синие, без серых «ореолов»
    ink = a[alpha > 200]
    assert len(ink) > 2000
    assert np.median(ink[:, 2] - ink[:, 0]) > 60
    semi = a[(alpha > 30) & (alpha < 200)]
    assert len(semi) == 0 or np.median(semi[:, 2] - semi[:, 0]) > 40  # края тоже синие, не серые
    # пыль сканера не осталась (вне кольца нет одиночных непрозрачных пикселей)
    yy, xx = np.mgrid[0:h, 0:w]
    rr = np.hypot(yy - h / 2, xx - w / 2)
    outside = (rr > 0.5 * min(h, w)) & (alpha > 60)
    assert outside.sum() < 10


def test_threshold_slider_changes_result(fx):
    src = images.open_image(open(fx["stamp"], "rb").read())
    lo = np.asarray(images.remove_background(src, threshold=0.06))[..., 3]
    hi = np.asarray(images.remove_background(src, threshold=0.35))[..., 3]
    assert (lo > 128).sum() > (hi > 128).sum()


def test_stamp_lossless_with_alpha_on_all_pages(fx, tmp_path):
    lib = Library()
    png = images.to_png(images.remove_background(images.open_image(open(fx["stamp"], "rb").read())))
    it = lib.add(png, "stamp")
    assert lib.items()[0]["id"] == it["id"]
    m = DocModel.open(fx["pages"])
    aid = m.add_asset(lib.png(it["id"]))
    for ref in m.pages:
        w, h = m.display_size(ref)
        cx, cy = m.to_unrotated(ref, w * 0.7, h * 0.8)
        m.add_obj(ref["id"], {"t": "image", "asset": aid, "cx": cx, "cy": cy, "w": 113, "h": 113,
                              "angle": m.upright_angle(ref)}, commit=False)
    m.commit()
    out = tmp_path / "o.pdf"
    saver.save(m.sources, m.job_state(), m.assets, str(out))
    d = fitz.open(out)
    xrefs = set()
    for p in d:
        imgs = p.get_images(full=True)
        assert imgs, "нет печати на листе"
        xref, smask = imgs[0][0], imgs[0][1]
        assert smask > 0, "нет прозрачности (SMask)"
        xrefs.add(xref)
        info = d.extract_image(xref)
        assert info["ext"] == "png" or d.xref_get_key(xref, "Filter")[1] == "/FlateDecode"
    # картинка встроена один раз на все листы, в исходном разрешении
    assert len(xrefs) == 1
    pix = fitz.Pixmap(d, xrefs.pop())
    src = Image.open(io.BytesIO(png))
    assert (pix.width, pix.height) == src.size


def test_drawn_signature_rgba(tmp_path):
    im = Image.new("RGBA", (400, 160), (0, 0, 0, 0))
    a = np.array(im)
    a[70:80, 30:370] = (26, 63, 163, 255)
    trimmed = images.trim(Image.fromarray(a))
    assert trimmed.size[1] < 40
    lib = Library()
    it = lib.add(images.to_png(trimmed), "signature")
    assert it["kind"] == "signature"
    assert Library().items()[0]["id"] == it["id"]  # хранится на диске
