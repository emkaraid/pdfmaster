"""Измерения на листе: масштаб, длина, площадь, точки привязки."""
from __future__ import annotations

import math
import re

import pymupdf as fitz

PT_MM = 25.4 / 72.0  # 1 пункт = 0,3528 мм на бумаге


def paper_mm(pt: float) -> float:
    return pt * PT_MM


def real_value(pt: float, scale: float) -> float:
    """Длина в метрах в натуре при масштабе 1:scale (pt — на листе)."""
    return paper_mm(pt) * scale / 1000.0


def length(pts) -> float:
    return sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(pts, pts[1:]))


def area(pts) -> float:
    """Площадь многоугольника (пт²), формула шнурования."""
    if len(pts) < 3:
        return 0.0
    s = 0.0
    for (x0, y0), (x1, y1) in zip(pts, pts[1:] + pts[:1]):
        s += x0 * y1 - x1 * y0
    return abs(s) / 2.0


def fmt_len(pt: float, scale: float) -> str:
    """«12,35 м» при масштабе, «24,7 мм» на бумаге при 1:1."""
    if scale and scale != 1:
        v = real_value(pt, scale)
        return (f"{v:.2f} м" if v < 1000 else f"{v / 1000:.3f} км").replace(".", ",")
    return f"{paper_mm(pt):.1f} мм".replace(".", ",")


def fmt_area(pt2: float, scale: float) -> str:
    mm2 = pt2 * PT_MM * PT_MM
    if scale and scale != 1:
        m2 = mm2 * scale * scale / 1e6
        return (f"{m2:.2f} м²" if m2 < 10000 else f"{m2 / 10000:.3f} га").replace(".", ",")
    return (f"{mm2 / 100:.2f} см²").replace(".", ",")


def _guess_scale(factor: float, unit: str) -> float | None:
    """Знаменатель масштаба 1:N по множителю /C (единиц на 1 пт) и единице /U."""
    unit = (unit or "").strip().lower()
    known = {"m": 1.0, "м": 1.0, "mm": 0.001, "мм": 0.001, "cm": 0.01, "см": 0.01, "km": 1000.0,
             "in": 0.0254, "ft": 0.3048}
    pt_m = PT_MM / 1000.0
    if unit in known:
        return factor * known[unit] / pt_m
    # единица не указана (так пишет AutoCAD): решаем, мм это или метры
    n_mm = factor * 0.001 / pt_m
    if 0.97 < n_mm < 1.03:
        return 1.0                      # лист в натуральную величину (мм на бумаге)
    n_m = n_mm * 1000.0
    if n_mm < 10 and 10 <= n_m <= 100000:
        return n_m                      # чертёж в метрах (планы, профили)
    return n_mm


def viewports(page: fitz.Page) -> list[dict]:
    """Видовые экраны с масштабом из словаря /VP /Measure (пишут AutoCAD, nanoCAD, Acrobat).

    [{'rect': fitz.Rect в неповёрнутых координатах листа, 'scale': N}] — от меньшего к большему.
    """
    doc = page.parent
    try:
        t, v = doc.xref_get_key(page.xref, "VP")
    except Exception:
        return []
    if t == "xref":
        v = doc.xref_object(int(v.split()[0]))
    elif t != "array":
        return []
    out = []
    m_pdf = page.transformation_matrix
    for vp in re.finditer(r"<<\s*/Type\s*/Viewport(.*?)/Measure\s*(<<.*?>>\s*>>|\d+\s+0\s+R)", v, re.S):
        body, mtxt = vp.group(1), vp.group(2)
        if mtxt.endswith("R"):
            mtxt = doc.xref_object(int(mtxt.split()[0]))
        bb = re.search(r"/BBox\s*\[([^\]]*)\]", body)
        xs = re.search(r"/X\s*\[(.*?)\]", mtxt, re.S)
        if not bb or not xs:
            continue
        nums = [float(x) for x in bb.group(1).split()[:4]]
        factor = 1.0
        cs = re.findall(r"/C\s*([-\d.eE]+)", xs.group(1))
        us = re.findall(r"/U\s*\((.*?)\)", xs.group(1))
        for c in cs:
            factor *= float(c)
        n = _guess_scale(factor, us[0] if us else "")
        if not n or n <= 0:
            continue
        if abs(n - round(n)) / n < 0.002:
            n = float(round(n))
        r = fitz.Rect(nums) * m_pdf
        out.append({"rect": r, "scale": round(n, 2)})
    out.sort(key=lambda d: d["rect"].width * d["rect"].height)
    return out


def scale_at(vps: list[dict], x: float, y: float) -> float | None:
    for vp in vps:
        if fitz.Point(x, y) in vp["rect"]:
            return vp["scale"]
    return None


_SCALE_RE = re.compile(r"(?:^|[\s(«\"])(?:[МM]|[Мм]асштаб)\.?\s*1\s*:\s*(\d{2,5})(?!\d)")


def text_scales(page: fitz.Page) -> list[dict]:
    """Масштабы, подписанные на листе текстом: «М 1:500», «Масштаб 1:1000».

    [{'scale': 500, 'axis': None|'h'|'v', 'text': 'М 1:500'}]. axis — если рядом
    сказано «по горизонтали/по вертикали» (профили)."""
    out = []
    try:
        blocks = page.get_text("dict")["blocks"]
    except Exception:
        return out
    for bl in blocks:
        for ln in bl.get("lines", ()):
            t = "".join(sp["text"] for sp in ln["spans"]).strip()
            for m in _SCALE_RE.finditer(" " + t):
                n = int(m.group(1))
                if not 10 <= n <= 100000:
                    continue
                tail = t.lower()
                axis = "v" if "вертик" in tail else ("h" if "горизонт" in tail else None)
                out.append({"scale": float(n), "axis": axis, "text": m.group(0).strip(" («\"")})
    return out


def sheet_scale(texts: list[dict]) -> tuple[float | None, str]:
    """Масштаб по надписям на листе: (N, откуда) или (None, подсказка).

    Берём, только если он однозначен: один масштаб (кроме «по вертикали»)."""
    plan = sorted({t["scale"] for t in texts if t["axis"] != "v"})
    allv = sorted({t["scale"] for t in texts})
    if len(plan) == 1 and len(allv) == 1:
        t = next(t for t in texts if t["scale"] == plan[0])
        return plan[0], f"надпись «{t['text']}» на листе"
    if allv:
        return None, "на листе подписаны масштабы " + ", ".join(f"1:{n:g}" for n in allv)
    return None, ""


def snap_points(page: fitz.Page, limit: int = 400000) -> list[float]:
    """Концы отрезков и углы прямоугольников (плоский список x,y в неповёрнутых координатах)."""
    out: list[float] = []
    try:
        paths = page.get_cdrawings()
    except Exception:
        return out
    for p in paths:
        for it in p.get("items", ()):
            k = it[0]
            if k == "l":
                out += [it[1][0], it[1][1], it[2][0], it[2][1]]
            elif k == "c":
                out += [it[1][0], it[1][1], it[4][0], it[4][1]]
            elif k == "re":
                x0, y0, x1, y1 = it[1]
                out += [x0, y0, x1, y0, x1, y1, x0, y1]
            elif k == "qu":
                for q in it[1]:
                    out += [q[0], q[1]]
        if len(out) > limit * 2:
            break
    return out
