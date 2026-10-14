"""Линейка: масштаб из файла AutoCAD, длина/площадь, привязка, запись размера в PDF."""

import pymupdf as fitz

from pdfmaster.core import measure as M
from pdfmaster.core.bake import BakeContext, bake_page


def _page_with_vp(factor: float, unit: str = " "):
    d = fitz.open()
    p = d.new_page(width=1190, height=842)
    vp = (f"[<</Type/Viewport/BBox[0 0 1190 842]/Measure<</Type/Measure/Subtype/RL/R( )/X[<</C .35279/U( )>>]"
          f"/D[<</C 1/U( )>>]/A[<</C 1/U( )>>]>>>>"
          f"<</Type/Viewport/BBox[56 170 1100 800]/Measure<</Type/Measure/Subtype/RL/R( )"
          f"/X[<</C {factor}/U({unit})>>]/D[<</C 1/U( )>>]/A[<</C 1/U( )>>]>>>>]")
    d.xref_set_key(p.xref, "VP", vp)
    p.draw_line((100, 300), (400, 300))
    return d, p


def test_autocad_viewport_scale():
    d, p = _page_with_vp(0.17639)          # так AutoCAD пишет план 1:500 (единицы — метры)
    vps = M.viewports(p)
    assert [v["scale"] for v in vps] == [500.0, 1.0]
    # точка в видовом экране — 1:500, на полях листа — 1:1
    assert M.scale_at(vps, 300, 300) == 500.0
    assert M.scale_at(vps, 10, 10) == 1.0
    d2, p2 = _page_with_vp(0.000352778 * 100, "m")   # явная единица «m»: 1:100
    assert M.viewports(p2)[0]["scale"] == 100.0


def test_lengths_and_area():
    pts = [(0, 0), (72 / 25.4 * 100, 0)]            # 100 мм на бумаге
    assert abs(M.paper_mm(M.length(pts)) - 100) < 1e-6
    assert M.fmt_len(M.length(pts), 500) == "50,00 м"
    assert M.fmt_len(M.length(pts), 1) == "100,0 мм"
    k = 72 / 25.4
    sq = [(0, 0), (100 * k, 0), (100 * k, 100 * k), (0, 100 * k)]   # квадрат 100×100 мм
    assert M.fmt_area(M.area(sq), 1000) == "10000,00 м²" or M.fmt_area(M.area(sq), 1000) == "1,000 га"
    assert abs(M.area(sq) * (25.4 / 72) ** 2 - 10000) < 1e-3


def test_snap_points_and_dimension_in_pdf(tmp_path):
    d, p = _page_with_vp(0.17639)
    flat = M.snap_points(p)
    pts = set(zip(flat[::2], flat[1::2]))
    assert (100.0, 300.0) in pts and (400.0, 300.0) in pts
    label = M.fmt_len(300.0, 500)
    bake_page(p, [{"t": "measure", "id": "m", "pts": [[100, 320], [400, 320]], "label": label}], BakeContext())
    out = tmp_path / "dim.pdf"
    d.save(out)
    t = fitz.open(out)[0].get_text()
    assert label in t
    assert abs(float(label.split()[0].replace(",", ".")) - 300 * 25.4 / 72 * 0.5) < 0.01


def test_scale_from_sheet_text():
    """Масштаб по надписи «М 1:500», если в файле его нет; у профиля (1:500 / 1:100) — не угадываем."""
    from pdfmaster.core import fonts as F
    d = fitz.open()
    ff = F.family_file("ptsans", False, False)
    plan = d.new_page()
    plan.insert_font(fontname="f", fontfile=ff)
    plan.insert_text((50, 800), "План трассы М 1:500", fontname="f", fontsize=10)
    prof = d.new_page()
    prof.insert_font(fontname="f", fontfile=ff)
    prof.insert_text((50, 100), "М 1:500 по горизонтали", fontname="f", fontsize=10)
    prof.insert_text((50, 120), "М 1:100 по вертикали", fontname="f", fontsize=10)
    n, why = M.sheet_scale(M.text_scales(d[0]))
    assert n == 500 and "1:500" in why
    n, why = M.sheet_scale(M.text_scales(d[1]))
    assert n is None and "1:100" in why and "1:500" in why
