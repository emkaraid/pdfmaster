"""Применение объектов правки к листу PDF («запекание»).

Одна и та же функция bake_page() используется и для предпросмотра (на копии
листа в процессе рендера), и при сохранении — поэтому на экране ровно то же,
что попадёт в файл.

Объекты (dict, координаты — неповёрнутый лист MuPDF, y вниз):
  edit   — правка строки/абзаца: {'t':'edit','lines':[line...],'text', 'style'|None, 'para':bool}
  text   — новый текст: {'t':'text','x','y','angle','text','family','bold','italic','size','color'}
  image  — картинка: {'t':'image','asset','cx','cy','w','h','angle'}
  erase  — удалить содержимое области: {'t':'erase','rect':[x0,y0,x1,y1],'white':bool}

Весь новый текст пишется напрямую в поток содержимого листа операторами
BT/TJ/ET с точными позициями глифов: шрифтом документа (теми же кодами) или
встроенным шрифтом-заменой (коды = номера глифов). Для замены программа сама
пишет таблицу /ToUnicode, чтобы копирование и поиск давали ровно введённый текст.
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass, field

import pymupdf as fitz

from . import fonts as F
from .textlines import color_int_to_rgb, hex_to_rgb

GOST_SLANT = math.tan(math.radians(15))
LINE_GAP = 1.2  # межстрочный интервал нового текста, в размерах шрифта


# ---------------------------------------------------------------- контекст


@dataclass
class BakeContext:
    assets: dict[str, bytes] = field(default_factory=dict)   # id -> PNG
    _imgdocs: dict[str, fitz.Document] = field(default_factory=dict)
    # xref шрифта-замены -> {gid: символ} — для /ToUnicode
    used_gids: dict[int, dict[int, str]] = field(default_factory=dict)

    def imgdoc(self, asset: str) -> fitz.Document | None:
        d = self._imgdocs.get(asset)
        if d is None:
            data = self.assets.get(asset)
            if not data:
                return None
            pix = fitz.Pixmap(data)
            w, h = pix.width, pix.height
            k = min(1.0, 14000.0 / max(w, h))
            d = fitz.open()
            pg = d.new_page(width=w * k, height=h * k)
            pg.insert_image(pg.rect, stream=data)
            self._imgdocs[asset] = d
        return d

    def finalize(self, doc: fitz.Document) -> None:
        """Переписывает /ToUnicode у вставленных шрифтов-замен."""
        for xref, gmap in self.used_gids.items():
            if not gmap:
                continue
            t, v = doc.xref_get_key(xref, "ToUnicode")
            old: dict[bytes, str] = {}
            if t == "xref":
                tx = int(v.split()[0])
                try:
                    old = F.parse_tounicode(doc.xref_stream(tx) or b"")
                except Exception:
                    old = {}
            else:
                tx = doc.get_new_xref()
                doc.update_object(tx, "<<>>")
                doc.xref_set_key(xref, "ToUnicode", f"{tx} 0 R")
            m = {int.from_bytes(k, "big"): s for k, s in old.items() if len(k) == 2}
            m.update(gmap)
            doc.update_stream(tx, _cmap(m))


def _cmap(m: dict[int, str]) -> bytes:
    items = sorted(m.items())
    out = ["/CIDInit /ProcSet findresource begin", "12 dict begin", "begincmap",
           "/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def",
           "/CMapName /Adobe-Identity-UCS def", "/CMapType 2 def",
           "1 begincodespacerange", "<0000> <FFFF>", "endcodespacerange"]
    for i in range(0, len(items), 100):
        chunk = items[i:i + 100]
        out.append(f"{len(chunk)} beginbfchar")
        for gid, s in chunk:
            out.append(f"<{gid:04X}> <{s.encode('utf-16-be').hex().upper()}>")
        out.append("endbfchar")
    out += ["endcmap", "CMapName currentdict /CMap defineresource pop", "end", "end"]
    return ("\n".join(out) + "\n").encode("ascii")


# ---------------------------------------------------------------- шрифтовые «стили»


@dataclass
class Style:
    """Чем писать фрагмент: шрифт документа или файл шрифта-замены."""
    size: float
    color: tuple[float, float, float]
    docfont: F.DocFont | None = None
    family: str = "sans"
    bold: bool = False
    italic: bool = False
    hscale: float = 1.0
    fontfile: str | None = None

    def font(self) -> fitz.Font:
        if self.fontfile:
            return F.load_font(self.fontfile)
        return F.substitute_font(self.family, self.bold, self.italic)

    @property
    def slant(self) -> bool:
        return self.docfont is None and F.font_synthetic_slant(self.family, self.italic)

    def can_encode(self, text: str) -> bool:
        if self.docfont is not None:
            return all(self.docfont.can_encode(ch) or ch == " " for ch in text)
        fnt = self.font()
        return all(ch == " " or fnt.has_glyph(F.glyph_cp(self.fontfile, ch)) for ch in text)

    def advance(self, ch: str) -> float:
        if self.docfont is not None:
            if ch in self.docfont.to_code:
                return self.docfont.advance(ch, self.size) * self.hscale
            if ch == " ":
                return 0.28 * self.size * self.hscale
            return 0.5 * self.size
        return self.font().glyph_advance(F.glyph_cp(self.fontfile, ch)) * self.size * self.hscale

    def width(self, text: str) -> float:
        return sum(self.advance(ch) for ch in text)

    def ascender(self) -> float:
        if self.docfont is not None:
            return self.docfont.ascender
        return self.font().ascender

    def descender(self) -> float:
        if self.docfont is not None:
            return self.docfont.descender
        return self.font().descender


def _span_style(pf: F.PageFonts, span: dict, sample: str, need: str) -> Style:
    """Стиль исходного фрагмента; шрифт документа, если он кодирует need."""
    df = pf.pick(span["font"], need)
    st = Style(size=span["size"], color=color_int_to_rgb(span["color"]), docfont=df,
               family=F.guess_family(span["font"]),
               bold=F.guess_bold(span["font"], span["flags"]),
               italic=F.guess_italic(span["font"], span["flags"]))
    if df is None or not st.can_encode(need):
        st.docfont = None
        st.fontfile = F.substitute_file(span["font"], span["flags"], need)
        _match_substitute(pf, span, sample, st)
    return st


def _match_substitute(pf: F.PageFonts, span: dict, sample: str, st: Style) -> None:
    """Подгоняет замену под шрифт документа: высоту заглавных — кеглем, ширину букв — Tz.

    Без этого замена того же кегля заметно крупнее или мельче соседних строк
    (ISOCPEUR → osifont: заглавные 0,645 кегля против ~0,72)."""
    df = pf.find(span["font"], sample)
    if df is None or not st.fontfile:
        return
    # замена — тот же шрифт по имени (Liberation вместо Liberation, Times вместо Times): не трогаем
    ex = F.exact_font_file(span["font"], span["flags"])
    if ex and os.path.abspath(ex) == os.path.abspath(st.fontfile):
        return
    k = 1.0
    sub_cap = F.cap_height(st.fontfile)
    # /CapHeight в PDF бывает неверным (так пишет, например, Qt под Windows) — берём только правдоподобное
    if df.cap_height and sub_cap and 0.5 <= df.cap_height <= 0.78:
        k = min(1.25, max(0.75, df.cap_height / sub_cap))
        if abs(k - 1.0) < 0.06:   # тот же шрифт из системы или близкий — не трогаем
            k = 1.0
    fnt = st.font()
    ratios = []
    for ch in set(sample) | set(df.to_code):
        if ch.isspace() or ch not in df.to_code:
            continue
        cp = F.glyph_cp(st.fontfile, ch)
        a = fnt.glyph_advance(cp) if fnt.has_glyph(cp) else 0.0
        if a > 0.05:
            ratios.append(df.advance(ch, 1.0) / a)
    h = 1.0
    if len(ratios) >= 5:
        ratios.sort()
        h = min(1.4, max(0.7, ratios[len(ratios) // 2] / k))
        if abs(h - 1.0) < 0.04:
            h = 1.0
    st.size = round(st.size * k, 3)
    st.hscale = h


def _override_style(pf: F.PageFonts, base_span: dict, sample: str, need: str, style: dict) -> Style:
    """Стиль, заданный пользователем в панели свойств."""
    size = float(style.get("size") or base_span["size"])
    color = hex_to_rgb(style["color"]) if style.get("color") else color_int_to_rgb(base_span["color"])
    bold = bool(style.get("bold", F.guess_bold(base_span["font"], base_span["flags"])))
    italic = bool(style.get("italic", F.guess_italic(base_span["font"], base_span["flags"])))
    fam = style.get("family") or "orig"
    if fam == "orig":
        st = _span_style(pf, base_span, sample, need)
        st.size, st.color = size, color
        if bold != st.bold or italic != st.italic:
            # тот же шрифт нужного начертания, если он есть в документе
            st.docfont = None
            for name in list(pf._by_name):
                if F.guess_family(name) == st.family and F.guess_bold(name) == bold \
                        and F.guess_italic(name) == italic:
                    df = pf.find(name)
                    if df is not None and all(df.can_encode(ch) or ch == " " for ch in need):
                        st.docfont = df
                        break
            if st.docfont is None:
                st.fontfile = F.substitute_file(base_span["font"], 0, need, bold=bold, italic=italic)
        st.bold, st.italic = bold, italic
        return st
    return Style(size=size, color=color, docfont=None, family=fam, bold=bold, italic=italic,
                 fontfile=F.substitute_file("", 0, need, family=fam, bold=bold, italic=italic))


# ---------------------------------------------------------------- запись текста


def _fmt(v: float) -> str:
    s = f"{v:.4f}".rstrip("0").rstrip(".")
    return s if s not in ("", "-0") else "0"


def _append_content(page: fitz.Page, data: bytes) -> None:
    doc = page.parent
    xref = doc.get_new_xref()
    doc.update_object(xref, "<<>>")
    doc.update_stream(xref, data)
    conts = page.get_contents()
    doc.xref_set_key(page.xref, "Contents", "[" + " ".join(f"{x} 0 R" for x in conts + [xref]) + "]")


def _font_resname(page: fitz.Page, font_xref: int) -> str:
    """Регистрирует шрифт документа в ресурсах листа и возвращает имя ресурса."""
    doc = page.parent
    name = f"PDFM{font_xref}"
    t, v = doc.xref_get_key(page.xref, "Resources")
    if t == "null":
        # ресурсы унаследованы от родителя — привяжем их к листу явно
        node = page.xref
        for _ in range(32):
            pt, pv = doc.xref_get_key(node, "Parent")
            if pt != "xref":
                break
            node = int(pv.split()[0])
            rt, rv = doc.xref_get_key(node, "Resources")
            if rt != "null":
                doc.xref_set_key(page.xref, "Resources", rv)
                break
        t, v = doc.xref_get_key(page.xref, "Resources")
        if t == "null":
            doc.xref_set_key(page.xref, "Resources", "<<>>")
            t, v = doc.xref_get_key(page.xref, "Resources")
    if t == "xref":
        holder, path = int(v.split()[0]), "Font"
    else:
        holder, path = page.xref, "Resources/Font"
    ft, fv = doc.xref_get_key(holder, path)
    if ft == "xref":
        doc.xref_set_key(int(fv.split()[0]), name, f"{font_xref} 0 R")
    else:
        doc.xref_set_key(holder, f"{path}/{name}", f"{font_xref} 0 R")
    return name


@dataclass
class Run:
    style: Style
    glyphs: list[tuple[str, float]]  # (символ, расстояние вдоль строки от origin)
    origin: tuple[float, float]
    d: tuple[float, float]


class Writer:
    """Пишет прогоны текста в поток содержимого листа."""

    def __init__(self, page: fitz.Page, ctx: BakeContext):
        self.page = page
        self.ctx = ctx
        self._wrapped = False
        self._subst: dict[str, tuple[str, int]] = {}
        self.pdf_m = ~page.transformation_matrix  # MuPDF -> PDF

    def _subst_res(self, path: str) -> tuple[str, int]:
        if path not in self._subst:
            name = f"PDFMs{len(self._subst) + 1}"
            xref = self.page.insert_font(fontname=name, fontfile=path)
            self._subst[path] = (name, xref)
        return self._subst[path]

    def write(self, run: Run) -> None:
        glyphs = [(" " if ch == "\t" else ch, s) for ch, s in run.glyphs if ch not in "\n\r"]
        if not glyphs:
            return
        st = run.style
        page = self.page
        size = st.size
        hs = st.hscale or 1.0
        if st.docfont is not None:
            df = st.docfont
            res = _font_resname(page, df.xref)

            def enc(ch):
                c = df.to_code.get(ch)
                return c.hex().upper() if c else None

            def adv(ch):
                return df.advance(ch, size) * hs if ch in df.to_code else 0.0
        else:
            path = st.fontfile or F.family_file(st.family, st.bold, st.italic)
            res, xref = self._subst_res(path)
            fnt = F.load_font(path)
            used = self.ctx.used_gids.setdefault(xref, {})

            def enc(ch):
                gid = fnt.has_glyph(F.glyph_cp(path, ch))
                if not gid:
                    return None
                used.setdefault(gid, ch)
                if used[gid] != ch and ch == " ":
                    used[gid] = ch  # пробел важнее неразрывного пробела
                return f"{gid:04X}"

            def adv(ch):
                cp = F.glyph_cp(path, ch)
                return fnt.glyph_advance(cp) * size * hs if fnt.has_glyph(cp) else 0.0

        if not self._wrapped:
            page.wrap_contents()
            self._wrapped = True
        m = self.pdf_m
        ox, oy = run.origin
        dx, dy = run.d
        o = fitz.Point(ox, oy) * m
        vx = dx * m.a + dy * m.c
        vy = dx * m.b + dy * m.d
        vl = math.hypot(vx, vy) or 1.0
        vx, vy = vx / vl, vy / vl
        items: list[str] = []
        cur = ""
        prev_s = None
        prev_adv = 0.0
        for ch, s in glyphs:
            code = enc(ch)
            if prev_s is not None:
                gap = (s - prev_s) - prev_adv
                adj = -gap * 1000.0 / (size * hs)
                if abs(adj) > 0.05:
                    if cur:
                        items.append(f"<{cur}>")
                        cur = ""
                    items.append(_fmt(adj))
            if code is not None:
                cur += code
                prev_adv = adv(ch)
            else:
                prev_adv = 0.0
            prev_s = s
        if cur:
            items.append(f"<{cur}>")
        if not any(i.startswith("<") for i in items):
            return
        first_s = glyphs[0][1]
        tx = o.x + first_s * vx
        ty = o.y + first_s * vy
        k = GOST_SLANT if st.slant else 0.0
        r, g, b = st.color
        content = "\n".join([
            "q BT",
            f"{_fmt(r)} {_fmt(g)} {_fmt(b)} rg 0 Tr 0 Tc 0 Tw 100 Tz 0 Ts",
            f"/{res} {_fmt(size)} Tf",
            f"{_fmt(vx * hs)} {_fmt(vy * hs)} {_fmt(-vy + k * vx)} {_fmt(vx + k * vy)} {_fmt(tx)} {_fmt(ty)} Tm",
            "[" + " ".join(items) + "] TJ",
            "ET Q", ""])
        _append_content(page, content.encode("latin-1"))


# ---------------------------------------------------------------- планирование правок


def _pos(line: dict, i: int) -> float:
    """Расстояние вдоль строки от origin первого символа до origin i-го."""
    c0 = line["chars"][0]
    c = line["chars"][i]
    d = line["dir"]
    return (c[1] - c0[1]) * d[0] + (c[2] - c0[2]) * d[1]


def _char_end(line: dict, i: int) -> float:
    c0 = line["chars"][0]
    c = line["chars"][i]
    d = line["dir"]
    pts = ((c[3], c[4]), (c[5], c[6]), (c[3], c[6]), (c[5], c[4]))
    return max((x - c0[1]) * d[0] + (y - c0[2]) * d[1] for x, y in pts)


def _estimate_hscale(pf: F.PageFonts, line: dict, si: int) -> float:
    """Оценка горизонтального масштаба (Tz) фрагмента по реальным позициям глифов."""
    chars = line["chars"]
    span = line["spans"][si]
    sample = "".join(c[0] for c in chars if c[7] == si)
    df = pf.find(span["font"], sample)
    if df is None:
        return 1.0
    ratios = []
    for k in range(len(chars) - 1):
        a, b = chars[k], chars[k + 1]
        if a[7] != si or b[7] != si or a[0] == " " or b[0] == " " or a[0] not in df.to_code:
            continue
        nat = df.advance(a[0], span["size"])
        if nat <= 0.01:
            continue
        ratios.append((_pos(line, k + 1) - _pos(line, k)) / nat)
    if len(ratios) < 3:
        return 1.0
    ratios.sort()
    med = ratios[len(ratios) // 2]
    return med if abs(med - 1) > 0.03 and 0.3 < med < 3 else 1.0


def _orig_runs(pf, line, idxs, shift, origin, d, span_text) -> list[Run] | None:
    """Прогоны для неизменённых символов (на исходных позициях + shift).

    None — если какой-то фрагмент нельзя повторить шрифтом документа.
    """
    runs: list[Run] = []
    chars = line["chars"]
    cur_key, cur = None, []
    hs_cache: dict = {}

    def flush():
        if cur:
            si, df = cur_key
            span = line["spans"][si]
            st = Style(size=span["size"], color=color_int_to_rgb(span["color"]), docfont=df,
                       family=F.guess_family(span["font"]), bold=F.guess_bold(span["font"], span["flags"]),
                       italic=F.guess_italic(span["font"], span["flags"]))
            if si not in hs_cache:
                hs_cache[si] = _estimate_hscale(pf, line, si)
            st.hscale = hs_cache[si]
            runs.append(Run(st, list(cur), origin, d))

    prev_df = None
    for i in idxs:
        c = chars[i]
        span = line["spans"][c[7]]
        if c[0].isspace() and prev_df is not None and cur_key and cur_key[0] == c[7]:
            df = prev_df  # пробел — в том же прогоне (без глифа просто сдвиг)
        else:
            # среди одноимённых шрифтов документа берём тот, что кодирует символ
            df = pf.pick(span["font"], c[0], prefer=prev_df)
            if df is None:
                return None
        key = (c[7], df)
        if key != cur_key:
            flush()
            cur, cur_key = [], key
        cur.append((c[0], _pos(line, i) + shift))
        prev_df = df
    flush()
    return runs


def plan_line_edit(pf: F.PageFonts, line: dict, new: str, style: dict | None):
    """Возвращает (индексы удаляемых символов, прогоны нового текста)."""
    old = line["text"]
    chars = line["chars"]
    spans = line["spans"]
    n = len(old)
    d = tuple(line["dir"])
    origin = (chars[0][1], chars[0][2])
    new = new.replace("\n", " ")
    span_text = {si: "".join(c[0] for c in chars if c[7] == si) for si in range(len(spans))}

    if style:
        st = _override_style(pf, spans[chars[0][7]], span_text[chars[0][7]], new, style)
        if st.docfont is not None:
            st.hscale = _estimate_hscale(pf, line, chars[0][7])
        glyphs = []
        s = 0.0
        for ch in new:
            glyphs.append((ch, s))
            s += st.advance(ch)
        return list(range(n)), [Run(st, glyphs, origin, d)]

    p = 0
    while p < min(n, len(new)) and old[p] == new[p]:
        p += 1
    s = 0
    while s < min(n, len(new)) - p and old[n - 1 - s] == new[len(new) - 1 - s]:
        s += 1
    if p == n and len(new) == n:
        return [], []
    mid = new[p:len(new) - s]
    if p < n - s:
        si = chars[p][7]
    elif p > 0:
        si = chars[p - 1][7]
    else:
        si = chars[min(p, n - 1)][7]
    st = _span_style(pf, spans[si], span_text[si], mid)
    if st.docfont is not None:
        st.hscale = _estimate_hscale(pf, line, si)
    start = _pos(line, p) if p < n else _char_end(line, n - 1)
    end_old = _pos(line, n - s) if s > 0 else (_char_end(line, n - 1) if n else 0.0)
    delta = (start + st.width(mid)) - end_old
    mid_glyphs = []
    x = start
    for ch in mid:
        mid_glyphs.append((ch, x))
        x += st.advance(ch)
    mid_runs = [Run(st, mid_glyphs, origin, d)] if mid_glyphs else []

    # Предпочтительно переписать сегмент целиком (тем же шрифтом, на тех же
    # местах): тогда текст в файле идёт в логическом порядке и копируется/ищется
    # как одна строка. Если шрифт документа повторить нельзя — трогаем минимум.
    pre = _orig_runs(pf, line, range(0, p), 0.0, origin, d, span_text)
    suf = _orig_runs(pf, line, range(n - s, n), delta, origin, d, span_text)
    if pre is not None and suf is not None:
        return list(range(n)), pre + mid_runs + suf
    remove = list(range(p, n - s))
    runs = list(mid_runs)
    if s > 0 and abs(delta) > 0.02:
        remove += list(range(n - s, n))
        suf_runs = _orig_runs(pf, line, range(n - s, n), delta, origin, d, span_text)
        if suf_runs is None:  # шрифт документа недоступен — замена, по прогону на фрагмент
            suf_runs = []
            cur_si, cur = None, []
            for i in list(range(n - s, n)) + [None]:
                si = chars[i][7] if i is not None else None
                if si != cur_si and cur:
                    txt = "".join(g[0] for g in cur)
                    sst = _span_style(pf, spans[cur_si], span_text[cur_si], txt + "\uffff")
                    suf_runs.append(Run(sst, cur, origin, d))
                    cur = []
                if i is None:
                    break
                cur_si = si
                cur.append((chars[i][0], _pos(line, i) + delta))
        runs += suf_runs
    return remove, runs


def _wrap(st: Style, text: str, width: float, first_width: float | None = None) -> list[tuple[str, bool]]:
    """Перенос по словам. Возвращает [(строка, конец_абзаца)].
    first_width — ширина первой строки (у неё бывает абзацный отступ)."""
    out: list[tuple[str, bool]] = []
    for para in text.split("\n"):
        words = para.split(" ")
        cur = ""
        for w in words:
            cand = (cur + " " + w) if cur else w
            lim = first_width if (first_width is not None and not out) else width
            if cur and st.width(cand) > lim + 0.01:
                out.append((cur, False))
                cur = w
            else:
                cur = cand
        out.append((cur, True))
    return out


def para_geometry(lines: list[dict]) -> dict:
    """Геометрия абзаца: левый/правый край, шаг строк, выравнивание."""
    first = lines[0]
    d = first["dir"]
    up = (d[1], -d[0])
    O = (first["chars"][0][1], first["chars"][0][2])
    starts, ends, bases = [], [], []
    for ln in lines:
        c0 = ln["chars"][0]
        st = (c0[1] - O[0]) * d[0] + (c0[2] - O[1]) * d[1]
        starts.append(st)
        ends.append(st + _char_end(ln, len(ln["chars"]) - 1))
        bases.append(-((c0[1] - O[0]) * up[0] + (c0[2] - O[1]) * up[1]))
    size = first["spans"][first["chars"][0][7]]["size"]
    if len(lines) > 1:
        steps = sorted(b - a for a, b in zip(bases, bases[1:]) if b - a > 0.3 * size)
        step = steps[len(steps) // 2] if steps else LINE_GAP * size
    else:
        step = LINE_GAP * size
    left = min(starts[1:]) if len(starts) > 1 else starts[0]
    right = max(ends)
    # правый край колонки/ячейки (если известен): по нему и переносим — так строка,
    # уже вылезшая за поле, вернётся в колонку, а одиночная строка получит место
    from .textlines import column_limit_from
    lims = [v for v in (column_limit_from(ln, O, d) for ln in lines) if v is not None]
    col_right = min(lims) if lims else None
    align = "left"
    ref = col_right if col_right is not None else right
    if len(lines) >= 2 and all(abs(e - ref) < 1.5 or e > ref for e in ends[:-1]):
        align = "justify"
    elif len(lines) > 1:
        centers = [(a + b) / 2 for a, b in zip(starts, ends)]
        if max(centers) - min(centers) < 1.5 and max(starts) - min(starts) > 3:
            align = "center"
    if col_right is not None and align != "center" and col_right > left + 10:
        right = col_right if len(lines) > 1 else max(right, col_right)
    return {"origin": O, "dir": d, "up": up, "left": left, "right": right,
            "indent": starts[0] - left, "step": step, "align": align}


def plan_para_edit(pf: F.PageFonts, lines: list[dict], new: str, style: dict | None):
    g = para_geometry(lines)
    first = lines[0]
    si = first["chars"][0][7]
    span = first["spans"][si]
    sample = "".join(c[0] for c in first["chars"] if c[7] == si)
    need = new.replace("\n", "")
    if style:
        st = _override_style(pf, span, sample, need, style)
    else:
        st = _span_style(pf, span, sample, need)
    if st.docfont is not None:
        st.hscale = _estimate_hscale(pf, first, si)
    if style and style.get("size") and abs(float(style["size"]) - span["size"]) > 0.01:
        g["step"] *= float(style["size"]) / span["size"]
    d = g["dir"]
    O = g["origin"]
    runs: list[Run] = []
    width = max(10.0, g["right"] - g["left"])
    first_w = max(10.0, width - max(0.0, g["indent"]))
    rows = _wrap(st, new, width, first_w)
    if style and style.get("fit") and len(rows) > len(lines):
        # «уместить в прежние строки»: уменьшаем шрифт (и шаг строк) до 70 %
        base_size, base_step = st.size, g["step"]
        while len(rows) > len(lines) and st.size > base_size * 0.7:
            st.size = round(st.size - 0.25, 2)
            rows = _wrap(st, new, width, first_w)
        g["step"] = base_step * min(1.0, max(st.size / base_size, 0.85))
    for k, (txt, last) in enumerate(rows):
        x0 = g["left"] + (g["indent"] if k == 0 else 0.0)
        avail = g["right"] - x0
        nat = st.width(txt)
        gaps = txt.count(" ")
        extra_space = 0.0
        if g["align"] == "justify" and not last and gaps:
            extra_space = max(0.0, (avail - nat) / gaps)
        elif g["align"] == "center":
            x0 += max(0.0, (avail - nat) / 2)
        down = k * g["step"]
        lo = (O[0] - g["up"][0] * down, O[1] - g["up"][1] * down)
        glyphs = []
        x = x0
        for ch in txt:
            glyphs.append((ch, x))
            x += st.advance(ch) + (extra_space if ch == " " else 0.0)
        if glyphs:
            runs.append(Run(st, glyphs, lo, tuple(d)))
    plan_para_edit.last_rows = len(rows)
    plan_para_edit.last_size = st.size
    return [list(range(len(ln["chars"]))) for ln in lines], runs


def para_rows(page: fitz.Page, lines: list[dict], new: str, style: dict | None) -> tuple[int, float]:
    """Сколько строк займёт абзац после правки и каким кеглем (для подсказки в интерфейсе)."""
    plan_para_edit(F.PageFonts(page), lines, new, style)
    return plan_para_edit.last_rows, plan_para_edit.last_size


def _char_redact_rect(c) -> fitz.Rect:
    r = fitz.Rect(c[3], c[4], c[5], c[6])
    w, h = r.width, r.height
    return fitz.Rect(r.x0 + 0.25 * w, r.y0 + 0.3 * h, r.x1 - 0.25 * w, r.y1 - 0.3 * h)


# ---------------------------------------------------------------- новый текст и картинки


def text_obj_style(o: dict) -> Style:
    fam = o.get("family", "serif")
    bold, italic = bool(o.get("bold")), bool(o.get("italic"))
    return Style(size=float(o.get("size", 12)), color=hex_to_rgb(o.get("color", "#000000")),
                 docfont=None, family=fam, bold=bold, italic=italic,
                 fontfile=F.substitute_file("", 0, o.get("text", ""), family=fam, bold=bold, italic=italic))


def text_obj_layout(o: dict) -> tuple[list[Run], float, float]:
    """Прогоны для объекта «новый текст», а также ширина и высота блока."""
    st = text_obj_style(o)
    a = math.radians(float(o.get("angle", 0.0)))
    d = (math.cos(a), math.sin(a))
    down = (-d[1], d[0])
    asc = st.ascender() * st.size
    step = LINE_GAP * st.size
    runs = []
    width = 0.0
    lines = o.get("text", "").split("\n")
    for k, ln in enumerate(lines):
        off = asc + k * step
        org = (o["x"] + down[0] * off, o["y"] + down[1] * off)
        glyphs = []
        x = 0.0
        for ch in ln:
            glyphs.append((ch, x))
            x += st.advance(ch)
        width = max(width, x)
        if glyphs:
            runs.append(Run(st, glyphs, org, d))
    height = (len(lines) - 1) * step + st.size * (st.ascender() - st.descender())
    return runs, width, height


def image_rect(o: dict) -> tuple[fitz.Rect, float]:
    a = math.radians(float(o.get("angle", 0.0)))
    w, h = float(o["w"]), float(o["h"])
    W = abs(w * math.cos(a)) + abs(h * math.sin(a))
    H = abs(w * math.sin(a)) + abs(h * math.cos(a))
    cx, cy = float(o["cx"]), float(o["cy"])
    return fitz.Rect(cx - W / 2, cy - H / 2, cx + W / 2, cy + H / 2), float(o.get("angle", 0.0))


def _bake_measure(page: fitz.Page, o: dict, w: "Writer") -> None:
    """Размер/измерение: линия с засечками и подпись над серединой первого отрезка."""
    pts = [fitz.Point(p) for p in o["pts"]]
    if len(pts) < 2:
        return
    col = hex_to_rgb(o.get("color", "#d6452f"))
    sh = page.new_shape()
    seq = pts + ([pts[0]] if o.get("closed") else [])
    sh.draw_polyline(seq)
    sh.finish(color=col, width=0.7, closePath=False)
    # засечки на концах (как на размерной линии)
    for a, b in ((pts[0], pts[1]), (pts[-1], pts[-2])):
        v = b - a
        ln = math.hypot(v.x, v.y) or 1.0
        n = fitz.Point(-v.y / ln, v.x / ln) * 4.0
        t = fitz.Point(v.x / ln, v.y / ln) * 2.5
        sh.draw_line(a - n - t, a + n + t)
    sh.finish(color=col, width=0.7)
    sh.commit()
    label = o.get("label", "")
    if not label:
        return
    a, b = pts[0], pts[1]
    ang = math.atan2(b.y - a.y, b.x - a.x)
    if math.cos(ang) < -1e-6 or (abs(math.cos(ang)) < 1e-6 and math.sin(ang) > 0):
        a, b = b, a  # подпись читается слева направо / снизу вверх
        ang = math.atan2(b.y - a.y, b.x - a.x)
    d = (math.cos(ang), math.sin(ang))
    size = float(o.get("size", 7.0))
    st = Style(size=size, color=col, docfont=None, family="sans",
               fontfile=F.substitute_file("", 0, label, family="ptsans"))
    wdt = st.width(label)
    mid = fitz.Point((a.x + b.x) / 2, (a.y + b.y) / 2)
    up = fitz.Point(d[1], -d[0])  # «вверх» от линии
    org = mid - fitz.Point(d[0], d[1]) * (wdt / 2) + up * 2.0
    glyphs, x = [], 0.0
    for ch in label:
        glyphs.append((ch, x))
        x += st.advance(ch)
    w.write(Run(st, glyphs, (org.x, org.y), d))


# ---------------------------------------------------------------- главное


def bake_page(page: fitz.Page, objs: list[dict], ctx: BakeContext, skip: set | None = None,
              finalize: bool = True) -> None:
    """Применяет объекты к листу. skip — id объектов, которые не рисовать
    (например, редактируемый сейчас текст: удаление старого остаётся)."""
    if not objs:
        return
    skip = skip or set()
    pf = F.PageFonts(page)

    # 1. Планируем правки текста (до удаления — нужны исходные шрифты листа)
    plans: dict[int, list[Run]] = {}
    text_rects: list[fitz.Rect] = []
    for o in objs:
        if o.get("t") != "edit":
            continue
        lines = o["lines"]
        new = o.get("text", "")
        if o.get("para"):
            removes, runs = plan_para_edit(pf, lines, new, o.get("style"))
            for ln, rem in zip(lines, removes):
                text_rects += [_char_redact_rect(ln["chars"][i]) for i in rem]
        else:
            ln = lines[0]
            rem, runs = plan_line_edit(pf, ln, new, o.get("style"))
            text_rects += [_char_redact_rect(ln["chars"][i]) for i in rem]
        plans[id(o)] = runs

    # 2. Удаление областей (графика, картинки, текст)
    erase = [o for o in objs if o.get("t") == "erase"]
    if erase:
        for o in erase:
            page.add_redact_annot(fitz.Rect(o["rect"]), fill=(1, 1, 1) if o.get("white", True) else False,
                                  cross_out=False)
        page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_PIXELS,
                              graphics=fitz.PDF_REDACT_LINE_ART_REMOVE_IF_COVERED,
                              text=fitz.PDF_REDACT_TEXT_REMOVE)
    # 3. Удаление старого текста — только сам текст, графика и картинки не трогаются
    if text_rects:
        for r in text_rects:
            if r.is_valid and not r.is_empty:
                page.add_redact_annot(r, fill=False, cross_out=False)
        page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE,
                              graphics=fitz.PDF_REDACT_LINE_ART_NONE,
                              text=fitz.PDF_REDACT_TEXT_REMOVE)

    # 4. Новое содержимое — в порядке создания объектов
    w = Writer(page, ctx)
    for o in objs:
        if o.get("id") in skip:
            continue
        t = o.get("t")
        if t == "edit":
            for run in plans.get(id(o), []):
                w.write(run)
        elif t == "text":
            runs, _, _ = text_obj_layout(o)
            for run in runs:
                w.write(run)
        elif t == "measure":
            _bake_measure(page, o, w)
        elif t == "image":
            src = ctx.imgdoc(o["asset"])
            if src is None:
                continue
            rect, angle = image_rect(o)
            page.show_pdf_page(rect, src, 0, rotate=-angle, keep_proportion=True, overlay=True)
    if finalize:
        ctx.finalize(page.parent)
