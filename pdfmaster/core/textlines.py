"""Извлечение редактируемых строк текста с листа.

Строка MuPDF разбивается на «сегменты» по большим разрывам — иначе соседние
ячейки таблицы склеиваются в одну строку. Всё в координатах неповёрнутого
листа MuPDF (y вниз), как и остальные объекты программы.
"""
from __future__ import annotations

import math

import pymupdf as fitz

FLAGS = (fitz.TEXT_PRESERVE_WHITESPACE | fitz.TEXT_PRESERVE_LIGATURES
         | fitz.TEXT_MEDIABOX_CLIP)


def _seg_quad(chars, spans, d):
    """Четырёхугольник сегмента: [[x,y]*4] — низ-лево, низ-право, верх-право, верх-лево."""
    dx, dy = d
    up = (dy, -dx)
    o = chars[0]
    ox, oy = o[1], o[2]
    # протяжённость вдоль строки
    def along(px, py):
        return (px - ox) * dx + (py - oy) * dy
    start = min(min(along(c[3], c[4]), along(c[5], c[6]), along(c[3], c[6]), along(c[5], c[4])) for c in chars)
    end = max(max(along(c[3], c[4]), along(c[5], c[6]), along(c[3], c[6]), along(c[5], c[4])) for c in chars)
    asc = max(spans[c[7]]["size"] * spans[c[7]]["asc"] for c in chars)
    desc = max(spans[c[7]]["size"] * -spans[c[7]]["desc"] for c in chars)
    def pt(a, h):
        return [ox + a * dx + h * up[0], oy + a * dy + h * up[1]]
    return [pt(start, -desc), pt(end, -desc), pt(end, asc), pt(start, asc)]


def _separators(page: fitz.Page):
    """Тонкие вертикальные и горизонтальные линии листа (границы ячеек таблиц).

    Возвращает (vert, horiz): списки (координата, начало, конец), отсортированные.
    """
    vert, horiz = [], []
    try:
        paths = page.get_cdrawings()
    except Exception:
        return vert, horiz

    def seg(p, q):
        (x0, y0), (x1, y1) = p, q
        if abs(x1 - x0) < 0.8 and abs(y1 - y0) > 2:
            vert.append(((x0 + x1) / 2, min(y0, y1), max(y0, y1)))
        elif abs(y1 - y0) < 0.8 and abs(x1 - x0) > 2:
            horiz.append(((y0 + y1) / 2, min(x0, x1), max(x0, x1)))

    for path in paths:
        for it in path.get("items", ()):
            kind = it[0]
            if kind == "l":
                seg(it[1], it[2])
            elif kind == "re":
                x0, y0, x1, y1 = it[1]
                if x1 - x0 < 2.5 and y1 - y0 > 2:
                    vert.append(((x0 + x1) / 2, y0, y1))
                elif y1 - y0 < 2.5 and x1 - x0 > 2:
                    horiz.append(((y0 + y1) / 2, x0, x1))
                else:
                    seg((x0, y0), (x0, y1))
                    seg((x1, y0), (x1, y1))
                    seg((x0, y0), (x1, y0))
                    seg((x0, y1), (x1, y1))
            elif kind == "qu":
                q = it[1]
                pts = [q[0], q[1], q[3], q[2]]
                for a, b in zip(pts, pts[1:] + pts[:1]):
                    seg(a, b)
    vert.sort()
    horiz.sort()
    return vert, horiz


def _crosses(seps, lo: float, hi: float, a: float, b: float) -> bool:
    """Есть ли разделитель с координатой в (lo, hi), перекрывающий отрезок [a, b]."""
    import bisect
    i = bisect.bisect_left(seps, (lo, -1e9, -1e9))
    # граница ячейки перекрывает почти всю высоту символа и длиннее неё
    need = 0.85 * (b - a)
    while i < len(seps) and seps[i][0] < hi:
        c, s0, s1 = seps[i]
        if s1 - s0 >= 1.1 * (b - a) and min(s1, b) - max(s0, a) >= need:
            return True
        i += 1
    return False


def extract_lines(page: fitz.Page) -> list[dict]:
    """Список сегментов строк листа.

    Каждый: {'text', 'chars': [[c, ox, oy, x0, y0, x1, y1, span_i]...],
             'spans': [...], 'dir': [dx, dy], 'quad': [...], 'bbox': [...], 'block': n}
    """
    raw = page.get_text("rawdict", flags=FLAGS)
    seps = None
    out: list[dict] = []
    for bi, b in enumerate(raw.get("blocks", [])):
        if b.get("type", 0) != 0:
            continue
        for line in b.get("lines", []):
            d = line.get("dir", (1, 0))
            dl = math.hypot(*d) or 1.0
            d = (d[0] / dl, d[1] / dl)
            if line.get("wmode", 0):
                continue  # вертикальное письмо CJK — не поддерживаем
            spans = []
            chars = []
            for sp in line.get("spans", []):
                si = len(spans)
                spans.append({
                    "font": sp.get("font", ""), "size": float(sp.get("size", 10)),
                    "color": int(sp.get("color", 0)), "flags": int(sp.get("flags", 0)),
                    "asc": float(sp.get("ascender", 0.9)), "desc": float(sp.get("descender", -0.2)),
                    "alpha": int(sp.get("alpha", 255)),
                })
                for ch in sp.get("chars", []):
                    c = ch["c"]
                    if not c:
                        continue
                    x0, y0, x1, y1 = ch["bbox"]
                    chars.append([c, ch["origin"][0], ch["origin"][1], x0, y0, x1, y1, si])
            if not chars:
                continue
            # делим по большим разрывам
            segs: list[list] = [[chars[0]]]
            for prev, cur in zip(chars, chars[1:]):
                size = spans[cur[7]]["size"]
                # разрыв вдоль строки: от правого края предыдущего глифа до начала текущего
                prev_end = max((prev[3] - prev[1]) * d[0] + (prev[4] - prev[2]) * d[1],
                               (prev[5] - prev[1]) * d[0] + (prev[6] - prev[2]) * d[1],
                               (prev[3] - prev[1]) * d[0] + (prev[6] - prev[2]) * d[1],
                               (prev[5] - prev[1]) * d[0] + (prev[4] - prev[2]) * d[1])
                adv = (cur[1] - prev[1]) * d[0] + (cur[2] - prev[2]) * d[1]
                gap = adv - prev_end
                limit = 2.2 * size if prev[0] == " " or cur[0] == " " else 1.0 * size
                split = gap > limit or adv < -0.5 * size
                if not split and gap > -0.5 and (abs(d[1]) < 0.02 or abs(d[0]) < 0.02):
                    # граница ячейки таблицы между символами?
                    if seps is None:
                        seps = _separators(page)
                    if abs(d[1]) < 0.02:  # горизонтальная строка — ищем вертикальные линии
                        lo = min(prev[3], prev[5]) + 0.6 * abs(prev[5] - prev[3])
                        hi = max(cur[3], cur[5]) - 0.6 * abs(cur[5] - cur[3])
                        split = _crosses(seps[0], lo, max(lo, hi), min(cur[4], cur[6]), max(cur[4], cur[6]))
                    else:
                        ys = sorted((prev[4], prev[6], cur[4], cur[6]))
                        lo, hi = (ys[1], ys[2])
                        split = _crosses(seps[1], lo, hi, min(cur[3], cur[5]), max(cur[3], cur[5]))
                if split:
                    segs.append([cur])
                else:
                    segs[-1].append(cur)
            for seg in segs:
                # пробелы по краям сегмента не нужны
                while seg and seg[-1][0].isspace():
                    seg.pop()
                while seg and seg[0][0].isspace():
                    seg.pop(0)
                if not seg:
                    continue
                text = "".join(c[0] for c in seg)
                used = sorted({c[7] for c in seg})
                remap = {old: k for k, old in enumerate(used)}
                sp2 = [spans[i] for i in used]
                ch2 = [c[:7] + [remap[c[7]]] for c in seg]
                quad = _seg_quad(ch2, sp2, d)
                xs = [p[0] for p in quad]
                ys = [p[1] for p in quad]
                out.append({
                    "text": text, "chars": ch2, "spans": sp2, "dir": [d[0], d[1]],
                    "quad": quad, "bbox": [min(xs), min(ys), max(xs), max(ys)], "block": bi,
                })
    _set_limits(page, out, seps)
    return out


def _set_limits(page: fitz.Page, lines: list[dict], seps) -> None:
    """Для каждой строки — до какого места её можно продлить (правый край колонки
    текста или граница ячейки таблицы). line['limit'] — расстояние вдоль строки от
    origin первого символа; None — неизвестно (наклонный текст)."""
    if not lines:
        return
    if seps is None:
        seps = _separators(page)
    vert, horiz = seps
    pr = page.rect * page.derotation_matrix
    pw, ph = abs(pr.width), abs(pr.height)
    for ln in lines:
        d = ln["dir"]
        c0 = ln["chars"][0]
        size = ln["spans"][c0[7]]["size"]
        x0, y0, x1, y1 = ln["bbox"]
        if abs(d[1]) < 0.02 and d[0] > 0:      # обычная строка слева направо
            start, end, lo, hi, axis = c0[1], x1, y0, y1, 0
        elif abs(d[0]) < 0.02 and d[1] < 0:     # снизу вверх (вертикальная надпись)
            start, end, lo, hi, axis = c0[2], y0, x0, x1, 1
        elif abs(d[0]) < 0.02 and d[1] > 0:     # сверху вниз
            start, end, lo, hi, axis = c0[2], y1, x0, x1, 2
        else:
            ln["limit"] = None
            continue
        own = abs(end - start)
        # правый край «колонки»: строки с тем же направлением и почти тем же началом;
        # берём край, до которого доходят хотя бы две строки (выключка по ширине),
        # чтобы одиночные надписи штампа не раздвигали колонку
        ends = []
        for o in lines:
            if o is ln or o["dir"] != d:
                continue
            oc = o["chars"][0]
            if axis == 0 and abs(oc[1] - c0[1]) < 3 * size:
                ends.append(o["bbox"][2] - start)
            elif axis == 1 and abs(oc[2] - c0[2]) < 3 * size:
                ends.append(start - o["bbox"][1])
            elif axis == 2 and abs(oc[2] - c0[2]) < 3 * size:
                ends.append(o["bbox"][3] - start)
        ends.sort()
        rep = [e for i, e in enumerate(ends) if (i > 0 and e - ends[i - 1] < 1.5) or
               (i + 1 < len(ends) and ends[i + 1] - e < 1.5)]
        col = max([own] + (rep if rep else ends[-1:]))
        if col <= own + 0.5:  # одиночная строка: до симметричного поля листа
            if axis == 0:
                col = max(own, (pw - c0[1]) - c0[1] if c0[1] < pw / 2 else own)
            elif axis == 1:
                col = max(own, c0[2] - (ph - c0[2]) if c0[2] > ph / 2 else own)
        limit = col
        # граница ячейки таблицы справа от строки
        import bisect
        if axis == 0:
            seg, here = vert, end - 1
            i = bisect.bisect_left(seg, (here, -1e9, -1e9))
            while i < len(seg):
                c, a0, a1 = seg[i]
                if a0 <= (lo + hi) / 2 <= a1 and a1 - a0 >= (hi - lo) * 0.8:
                    limit = min(limit, c - 2 - start) if c - 2 - start >= own else limit
                    break
                i += 1
        elif axis == 1:
            seg = horiz
            best = None
            for c, a0, a1 in seg:
                if c < end + 1 and a0 <= (lo + hi) / 2 <= a1 and (best is None or c > best):
                    best = c
            if best is not None and start - (best + 2) >= own:
                limit = min(limit, start - (best + 2))
        ln["limit"] = max(own, limit)


def column_limit_from(line: dict, origin, d) -> float | None:
    """Предел строки как расстояние вдоль d от точки origin."""
    if line.get("limit") is None:
        return None
    c0 = line["chars"][0]
    return (c0[1] - origin[0]) * d[0] + (c0[2] - origin[1]) * d[1] + line["limit"]


def paragraph_of(lines: list[dict], line: dict) -> list[dict]:
    """Строки абзаца, в который входит line (сверху вниз), по геометрии.

    Соседняя строка того же направления с близким левым краем и обычным шагом
    принадлежит абзацу, если предыдущая строка дошла до правого края (иначе там
    конец абзаца), а следующая не начинается с абзацного отступа.
    """
    d = line["dir"]
    up = (d[1], -d[0])
    size = line["spans"][line["chars"][0][7]]["size"]

    def along(l):
        c = l["chars"][0]
        return c[1] * d[0] + c[2] * d[1]

    def base(l):  # координата базовой линии «вверх»
        c = l["chars"][0]
        return c[1] * up[0] + c[2] * up[1]

    def end(l):
        return along(l) + max(0.0, _extent(l))

    def right(l):
        lim = l.get("limit")
        return along(l) + lim if lim else end(l)

    same = [l for l in lines if l["dir"] == d and abs(l["spans"][l["chars"][0][7]]["size"] - size) < 0.6]

    def full(l):
        return end(l) >= right(l) - 1.6 * size

    def neighbour(cur, direction):
        best = None
        for l in same:
            if l is cur:
                continue
            db = (base(cur) - base(l)) * direction  # >0 — ниже (direction=1) / выше (-1)
            if not (0.5 * size < db < 1.9 * size):
                continue
            if abs(along(l) - along(cur)) > 3.5 * size:
                continue
            if best is None or db < best[0]:
                best = (db, l)
        return best[1] if best else None

    res = [line]
    cur = line
    while True:  # вверх
        prev = neighbour(cur, -1)
        if prev is None or not full(prev):
            break
        if along(cur) - along(prev) > 0.8 * size:  # cur с абзацным отступом — начало абзаца
            break
        res.insert(0, prev)
        cur = prev
    cur = line
    while full(cur):  # вниз
        nxt = neighbour(cur, 1)
        if nxt is None or along(nxt) - min(along(x) for x in res) > 0.8 * size:
            break
        res.append(nxt)
        cur = nxt
    return res


def _extent(l) -> float:
    c0 = l["chars"][0]
    d = l["dir"]
    return max((x - c0[1]) * d[0] + (y - c0[2]) * d[1]
               for c in l["chars"] for x, y in ((c[3], c[4]), (c[5], c[6]), (c[3], c[6]), (c[5], c[4])))


def join_para_text(texts: list[str]) -> str:
    """Склеивает строки абзаца: перенос «слово-» + «продолжение» — без пробела."""
    out = ""
    for t in texts:
        t = t.strip()
        if not t:
            continue
        if not out:
            out = t
        elif out.endswith("\u00ad"):
            out = out[:-1] + t
        elif out.endswith("-") and len(out) > 1 and out[-2].isalpha() and t[:1].isalpha() and t[:1].islower():
            out += t
        else:
            out += " " + t
    return out


def line_key(line: dict) -> str:
    c = line["chars"][0]
    return f"{c[1]:.2f},{c[2]:.2f},{len(line['chars'])}"


def color_int_to_rgb(c: int) -> tuple[float, float, float]:
    return (((c >> 16) & 255) / 255.0, ((c >> 8) & 255) / 255.0, (c & 255) / 255.0)


def rgb_to_hex(rgb) -> str:
    return "#%02x%02x%02x" % tuple(max(0, min(255, round(v * 255))) for v in rgb)


def hex_to_rgb(h: str) -> tuple[float, float, float]:
    h = h.lstrip("#")
    return (int(h[0:2], 16) / 255.0, int(h[2:4], 16) / 255.0, int(h[4:6], 16) / 255.0)
