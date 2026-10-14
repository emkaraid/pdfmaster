"""Шрифты: повторное использование встроенных шрифтов документа и подбор замены.

Встроенный шрифт PDF почти всегда «подмножество» (subset) без юникодной
таблицы cmap, поэтому fitz.Font(...).has_glyph() для него бесполезен.
Мы работаем на уровне PDF: читаем /ToUnicode (код -> символ), строим обратную
таблицу символ -> код и ширины глифов из /Widths или /W. Если все нужные
символы есть в подмножестве, новый текст пишется тем же самым шрифтовым
ресурсом — выглядит идентично оригиналу.
"""
from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field
from functools import lru_cache

import pymupdf as fitz

# ---------------------------------------------------------------- ресурсы


def resource_dir() -> str:
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return os.path.join(base, "pdfmaster", "resources")
    return os.path.join(os.path.dirname(os.path.dirname(__file__)), "resources")


FONT_DIR = os.path.join(resource_dir(), "fonts")
SYS_DIR = "/System/Library/Fonts/Supplemental"

# ключ семейства -> (подпись в интерфейсе, {стиль: [кандидаты-файлы]})
# стиль: "r", "b", "i", "bi". Сначала системный шрифт (настоящий Times/Arial),
# потом встроенная в программу свободная замена с кириллицей.
FAMILIES: dict[str, tuple[str, dict[str, list[str]]]] = {
    "serif": ("Times New Roman", {
        "r": [f"{SYS_DIR}/Times New Roman.ttf", "LiberationSerif-Regular.ttf"],
        "b": [f"{SYS_DIR}/Times New Roman Bold.ttf", "LiberationSerif-Bold.ttf"],
        "i": [f"{SYS_DIR}/Times New Roman Italic.ttf", "LiberationSerif-Italic.ttf"],
        "bi": [f"{SYS_DIR}/Times New Roman Bold Italic.ttf", "LiberationSerif-BoldItalic.ttf"],
    }),
    "sans": ("Arial", {
        "r": [f"{SYS_DIR}/Arial.ttf", "LiberationSans-Regular.ttf"],
        "b": [f"{SYS_DIR}/Arial Bold.ttf", "LiberationSans-Bold.ttf"],
        "i": [f"{SYS_DIR}/Arial Italic.ttf", "LiberationSans-Italic.ttf"],
        "bi": [f"{SYS_DIR}/Arial Bold Italic.ttf", "LiberationSans-BoldItalic.ttf"],
    }),
    "ptserif": ("PT Serif", {
        "r": ["PT_Serif-Web-Regular.ttf"], "b": ["PT_Serif-Web-Bold.ttf"],
        "i": ["PT_Serif-Web-Italic.ttf"], "bi": ["PT_Serif-Web-BoldItalic.ttf"],
    }),
    "ptsans": ("PT Sans", {
        "r": ["PT_Sans-Web-Regular.ttf"], "b": ["PT_Sans-Web-Bold.ttf"],
        "i": ["PT_Sans-Web-Italic.ttf"], "bi": ["PT_Sans-Web-BoldItalic.ttf"],
    }),
    "mono": ("Courier New", {
        "r": [f"{SYS_DIR}/Courier New.ttf", "LiberationMono-Regular.ttf"],
        "b": [f"{SYS_DIR}/Courier New Bold.ttf", "LiberationMono-Bold.ttf"],
        "i": [f"{SYS_DIR}/Courier New Italic.ttf", "LiberationMono-Italic.ttf"],
        "bi": [f"{SYS_DIR}/Courier New Bold Italic.ttf", "LiberationMono-BoldItalic.ttf"],
    }),
    "gost": ("ГОСТ (osifont)", {
        "r": ["osifont.ttf"], "b": ["osifont.ttf"], "i": ["osifont.ttf"], "bi": ["osifont.ttf"],
    }),
}
FAMILY_ORDER = ["serif", "sans", "ptserif", "ptsans", "gost", "mono"]

# Встроенные в программу варианты — используются, когда нужна воспроизводимость
# (тесты) или системного шрифта нет.
_only_bundled = os.environ.get("PDFM_BUNDLED_FONTS_ONLY") == "1"


def style_key(bold: bool, italic: bool) -> str:
    return ("b" if bold else "") + ("i" if italic else "") or "r"


@lru_cache(maxsize=None)
def family_file(family: str, bold: bool = False, italic: bool = False) -> str:
    fam = FAMILIES.get(family) or FAMILIES["sans"]
    for cand in fam[1][style_key(bold, italic)]:
        path = cand if os.path.isabs(cand) else os.path.join(FONT_DIR, cand)
        if _only_bundled and os.path.isabs(cand):
            continue
        if os.path.exists(path):
            return path
    # последний шанс — обычное начертание
    if bold or italic:
        return family_file(family, False, False)
    return os.path.join(FONT_DIR, "LiberationSans-Regular.ttf")


@lru_cache(maxsize=64)
def load_font(path: str) -> fitz.Font:
    return fitz.Font(fontfile=path)


def substitute_font(family: str, bold: bool, italic: bool) -> fitz.Font:
    return load_font(family_file(family, bold, italic))


_STYLE_WORDS = ("bolditalic", "boldoblique", "semibold", "demibold", "bold", "italic", "oblique",
                "regular", "roman", "book", "normal")


def _core_name(name: str) -> tuple[str, bool, bool]:
    """'TimesNewRomanPS-BoldItalicMT' -> ('timesnewroman', True, True)."""
    n = clean_font_name(name)
    fam, _, sty = re.split(r"([-,])", n, maxsplit=1) if re.search(r"[-,]", n) else (n, "", "")
    fam = fam.lower().replace(" ", "").replace("_", "")
    sty = sty.lower()
    bold = "bold" in sty or "black" in sty or "heavy" in sty
    italic = "italic" in sty or "oblique" in sty
    for suf in ("psmt", "mt", "ps"):
        if fam.endswith(suf) and len(fam) > len(suf) + 2:
            fam = fam[: -len(suf)]
            break
    # стилевые слова, приклеенные без дефиса: "Times New Roman Bold"
    changed = True
    while changed:
        changed = False
        for w in _STYLE_WORDS:
            if fam.endswith(w) and len(fam) > len(w) + 2:
                if "bold" in w:
                    bold = True
                if "italic" in w or "oblique" in w:
                    italic = True
                fam = fam[: -len(w)]
                changed = True
                break
    return fam, bold, italic


_INDEX: dict[tuple[str, bool, bool], str] | None = None


def _font_index() -> dict[tuple[str, bool, bool], str]:
    """Индекс шрифтов программы и системы по «ядру» имени и начертанию."""
    global _INDEX
    if _INDEX is not None:
        return _INDEX
    idx: dict[tuple[str, bool, bool], str] = {}
    dirs = [FONT_DIR]
    if not _only_bundled:
        dirs += [SYS_DIR, "/System/Library/Fonts", "/Library/Fonts", os.path.expanduser("~/Library/Fonts")]
    for d in dirs:
        try:
            files = sorted(os.listdir(d))
        except OSError:
            continue
        for fn in files:
            if not fn.lower().endswith((".ttf", ".otf")):
                continue
            path = os.path.join(d, fn)
            try:
                f = fitz.Font(fontfile=path)
                key = _core_name(f.name.replace(" ", "-", 0))
                # имя вида "Times New Roman Bold Italic": разбираем по словам
                words = f.name.lower().split()
                bold = any(w in ("bold", "black", "heavy", "semibold") for w in words) or f.is_bold
                italic = any(w in ("italic", "oblique") for w in words) or f.is_italic
                core = "".join(w for w in words if w not in _STYLE_WORDS)
                for k in ((core, bold, italic), (key[0], bold, italic)):
                    idx.setdefault(k, path)
            except Exception:
                continue
    _INDEX = idx
    return idx


def exact_font_file(fontname: str, flags: int = 0) -> str | None:
    """Файл того же шрифта (по имени), если он есть в программе или в системе."""
    core, bold, italic = _core_name(fontname)
    bold = bold or bool(flags & 16)
    italic = italic or bool(flags & 2)
    return _font_index().get((core, bold, italic))


def font_covers(path: str, text: str) -> bool:
    try:
        f = load_font(path)
    except Exception:
        return False
    return all(ch.isspace() or f.has_glyph(ord(ch)) for ch in text)


def substitute_file(fontname: str, flags: int, need: str, family: str | None = None,
                    bold: bool | None = None, italic: bool | None = None) -> str:
    """Подбирает файл шрифта-замены с нужными символами.

    Порядок: тот же шрифт по имени (из программы или системы) -> семейство
    по эвристике (Times -> Times New Roman/Liberation Serif и т.п.) -> PT.
    """
    b = guess_bold(fontname, flags) if bold is None else bold
    i = guess_italic(fontname, flags) if italic is None else italic
    cands = []
    if family is None:
        ex = exact_font_file(fontname, (16 if b else 0) | (2 if i else 0))
        if ex:
            cands.append(ex)
        family = guess_family(fontname)
    cands.append(family_file(family, b, i))
    fallback_fam = "ptserif" if family in ("serif", "ptserif") else "ptsans"
    cands += [family_file(fallback_fam, b, i), family_file("ptsans", False, False)]
    for c in cands:
        if font_covers(c, need):
            return c
    return cands[0]


def font_synthetic_slant(family: str, italic: bool) -> bool:
    """У ГОСТ-шрифта нет курсива: наклон 15° делаем матрицей."""
    return family == "gost" and italic


def clean_font_name(name: str) -> str:
    """'ABCDEF+TimesNewRomanPS-BoldMT' -> 'TimesNewRomanPS-BoldMT'."""
    if len(name) > 7 and name[6] == "+" and name[:6].isupper():
        name = name[7:]
    return name


def guess_family(fontname: str) -> str:
    n = clean_font_name(fontname).lower().replace(" ", "").replace("-", "").replace("_", "")
    if any(k in n for k in ("gost", "гост", "isocp", "iso3098", "osifont", "cs gost", "gostcommon")):
        return "gost"
    if "ptserif" in n:
        return "ptserif"
    if "ptsans" in n:
        return "ptsans"
    if any(k in n for k in ("courier", "mono", "consolas", "menlo", "lucidaconsole")):
        return "mono"
    if "sans" in n:
        return "sans"
    if any(k in n for k in ("times", "serif", "tinos", "georgia", "cambria", "garamond",
                            "bookantiqua", "palatino", "minion", "roman", "baskerville")):
        return "serif"
    return "sans"


def guess_bold(fontname: str, flags: int = 0) -> bool:
    n = clean_font_name(fontname).lower()
    return bool(flags & 16) or any(k in n for k in ("bold", "black", "heavy", "semibold", "demi", ",b"))


def guess_italic(fontname: str, flags: int = 0) -> bool:
    n = clean_font_name(fontname).lower()
    return bool(flags & 2) or any(k in n for k in ("italic", "oblique", "kursiv", ",i"))


# ---------------------------------------------------------------- разбор PDF-объектов

_TOKEN = re.compile(rb"\[|\]|-?\d*\.?\d+(?:[eE][-+]?\d+)?")


def _parse_number_array(text: str) -> list:
    """Разбирает '[0 [500 600] 10 20 300]' в вложенные списки чисел."""
    stack: list[list] = [[]]
    for m in _TOKEN.finditer(text.encode("latin-1", "replace")):
        tok = m.group(0)
        if tok == b"[":
            stack.append([])
        elif tok == b"]":
            if len(stack) > 1:
                arr = stack.pop()
                stack[-1].append(arr)
        else:
            v = float(tok)
            stack[-1].append(int(v) if v.is_integer() else v)
    res = stack[0]
    return res[0] if len(res) == 1 and isinstance(res[0], list) else res


def _get(doc: fitz.Document, xref: int, key: str) -> tuple[str, str]:
    try:
        return doc.xref_get_key(xref, key)
    except Exception:
        return ("null", "null")


def _resolve_array(doc: fitz.Document, xref: int, key: str) -> list | None:
    t, v = _get(doc, xref, key)
    if t == "xref":
        v = doc.xref_object(int(v.split()[0]), compressed=True)
        t = "array"
    if t != "array":
        return None
    return _parse_number_array(v)


def _resolve_number(doc: fitz.Document, xref: int, key: str) -> float | None:
    t, v = _get(doc, xref, key)
    if t == "xref":
        v = doc.xref_object(int(v.split()[0]), compressed=True).strip()
        t = "int"
    try:
        return float(v) if t in ("int", "float") or re.fullmatch(r"-?[\d.]+", v or "") else None
    except ValueError:
        return None


def _hex_to_bytes(h: bytes) -> bytes:
    h = re.sub(rb"\s", b"", h)
    if len(h) % 2:
        h += b"0"
    return bytes.fromhex(h.decode("ascii"))


def parse_tounicode(data: bytes) -> dict[bytes, str]:
    """CMap ToUnicode -> {код (байты): строка}."""
    res: dict[bytes, str] = {}

    def utf16(b: bytes) -> str:
        try:
            return b.decode("utf-16-be")
        except UnicodeDecodeError:
            return ""

    for block in re.finditer(rb"beginbfchar(.*?)endbfchar", data, re.S):
        for src, dst in re.findall(rb"<([0-9A-Fa-f\s]+)>\s*<([0-9A-Fa-f\s]*)>", block.group(1)):
            res[_hex_to_bytes(src)] = utf16(_hex_to_bytes(dst))
    for block in re.finditer(rb"beginbfrange(.*?)endbfrange", data, re.S):
        body = block.group(1)
        for m in re.finditer(rb"<([0-9A-Fa-f\s]+)>\s*<([0-9A-Fa-f\s]+)>\s*(<[0-9A-Fa-f\s]*>|\[[^\]]*\])", body):
            lo, hi = _hex_to_bytes(m.group(1)), _hex_to_bytes(m.group(2))
            n = len(lo)
            a, b = int.from_bytes(lo, "big"), int.from_bytes(hi, "big")
            if b < a or b - a > 65535:
                continue
            dst = m.group(3)
            if dst.startswith(b"["):
                items = re.findall(rb"<([0-9A-Fa-f\s]*)>", dst)
                for k, it in enumerate(items):
                    if a + k > b:
                        break
                    res[(a + k).to_bytes(n, "big")] = utf16(_hex_to_bytes(it))
            else:
                d = _hex_to_bytes(dst[1:-1])
                if not d:
                    continue
                base = int.from_bytes(d, "big")
                dl = len(d)
                for k in range(b - a + 1):
                    try:
                        res[(a + k).to_bytes(n, "big")] = utf16((base + k).to_bytes(dl, "big"))
                    except OverflowError:
                        break
    return res


_WIN_ANSI = {i: bytes([i]).decode("cp1252", "replace") for i in range(32, 256)}
_MAC_ROMAN = {i: bytes([i]).decode("mac_roman", "replace") for i in range(32, 256)}


def _simple_encoding(doc: fitz.Document, xref: int) -> dict[bytes, str]:
    """Кодировка простого шрифта без ToUnicode: базовая + /Differences."""
    t, v = _get(doc, xref, "Encoding")
    base = _WIN_ANSI
    diffs = None
    if t == "name":
        base = _MAC_ROMAN if "MacRoman" in v else _WIN_ANSI
    elif t in ("dict", "xref"):
        ex = int(v.split()[0]) if t == "xref" else None
        if ex:
            bt, bv = _get(doc, ex, "BaseEncoding")
            dt, dv = _get(doc, ex, "Differences")
        else:
            bt, bv = _get(doc, xref, "Encoding/BaseEncoding")
            dt, dv = _get(doc, xref, "Encoding/Differences")
        if bt == "name" and "MacRoman" in bv:
            base = _MAC_ROMAN
        if dt == "array":
            diffs = dv
    res = {bytes([k]): ch for k, ch in base.items()}
    if diffs:
        code = 0
        for tok in re.findall(r"/[^\s/\[\]]+|\d+", diffs):
            if tok.startswith("/"):
                try:
                    u = fitz.glyph_name_to_unicode(tok[1:])
                except Exception:
                    u = 0
                if u and u != 0xFFFD:
                    res[bytes([code])] = chr(u)
                code += 1
            else:
                code = int(tok)
    return res


@dataclass(eq=False)
class DocFont:
    """Шрифт документа, пригодный для записи нового текста теми же кодами."""
    xref: int
    basefont: str
    two_byte: bool
    to_code: dict[str, bytes] = field(default_factory=dict)   # символ -> код
    widths: dict[int, float] = field(default_factory=dict)    # код -> ширина (1/1000)
    default_width: float = 0.0
    ascender: float = 0.9
    descender: float = -0.2
    embedded: bool = True

    def can_encode(self, text: str) -> bool:
        return all(ch in self.to_code for ch in text)

    def code(self, ch: str) -> bytes:
        return self.to_code[ch]

    def advance(self, ch: str, size: float) -> float:
        c = self.to_code[ch]
        w = self.widths.get(int.from_bytes(c, "big"), self.default_width)
        return w * size / 1000.0

    def text_length(self, text: str, size: float) -> float:
        return sum(self.advance(ch, size) for ch in text)


def load_docfont(doc: fitz.Document, xref: int) -> DocFont | None:
    """Готовит шрифт документа к повторному использованию или возвращает None."""
    try:
        t, subtype = _get(doc, xref, "Subtype")
        _, basefont = _get(doc, xref, "BaseFont")
        basefont = basefont.lstrip("/")
        if subtype == "/Type0":
            _, enc = _get(doc, xref, "Encoding")
            if enc != "/Identity-H":
                return None
            desc = _resolve_array(doc, xref, "DescendantFonts")
            dt, dv = _get(doc, xref, "DescendantFonts")
            m = re.search(r"(\d+)\s+0\s+R", dv)
            if not m:
                return None
            dx = int(m.group(1))
            if dt == "xref":  # массив сам по себе косвенный
                m2 = re.search(r"(\d+)\s+0\s+R", doc.xref_object(dx))
                if not m2:
                    return None
                dx = int(m2.group(1))
            del desc
            dw = _resolve_number(doc, dx, "DW")
            widths: dict[int, float] = {}
            w = _resolve_array(doc, dx, "W") or []
            i = 0
            while i < len(w):
                first = w[i]
                if i + 1 < len(w) and isinstance(w[i + 1], list):
                    for k, val in enumerate(w[i + 1]):
                        widths[int(first) + k] = float(val)
                    i += 2
                elif i + 2 < len(w):
                    for c in range(int(first), int(w[i + 1]) + 1):
                        widths[c] = float(w[i + 2])
                    i += 3
                else:
                    break
            fdx = dx
            two = True
            default_w = dw if dw is not None else 1000.0
        elif subtype in ("/TrueType", "/Type1", "/MMType1"):
            fc = _resolve_number(doc, xref, "FirstChar")
            ws = _resolve_array(doc, xref, "Widths")
            if fc is None or not ws:
                return None
            widths = {int(fc) + k: float(v) for k, v in enumerate(ws) if not isinstance(v, list)}
            fdx = xref
            two = False
            mw = _resolve_number(doc, xref, "FontDescriptor/MissingWidth")
            default_w = mw or 0.0
        else:
            return None  # Type3 и прочая экзотика

        tt, tv = _get(doc, xref, "ToUnicode")
        if tt == "xref":
            cmap = parse_tounicode(doc.xref_stream(int(tv.split()[0])) or b"")
        elif not two:
            cmap = _simple_encoding(doc, xref)
        else:
            return None
        if not cmap:
            return None
        to_code: dict[str, bytes] = {}
        for code, s in cmap.items():
            if len(s) != 1:
                continue
            if two and len(code) != 2 or not two and len(code) != 1:
                continue
            ci = int.from_bytes(code, "big")
            if ci not in widths and default_w <= 0:
                continue
            # при дублях предпочитаем меньший код (обычно основной глиф)
            if s not in to_code or int.from_bytes(to_code[s], "big") > ci:
                to_code[s] = code
        # некоторые генераторы (Qt) отображают глиф пробела в U+0009 или U+00A0
        if " " not in to_code:
            for alt in ("\u00a0", "\t"):
                c = to_code.get(alt)
                if c is not None and 150 <= widths.get(int.from_bytes(c, "big"), default_w) <= 400:
                    to_code[" "] = c
                    break
        # Невстроенный простой шрифт тоже годится: исходный текст рядом нарисован им же,
        # и любой просмотрщик подставит для нового текста то же, что и для старого.
        ft, fv = _get(doc, fdx, "FontDescriptor")
        embedded = False
        if ft in ("xref", "dict"):
            for k in ("FontFile", "FontFile2", "FontFile3"):
                kt, _ = _get(doc, fdx, f"FontDescriptor/{k}")
                if kt == "xref":
                    embedded = True
        if not embedded and two:
            return None
        asc = _resolve_number(doc, fdx, "FontDescriptor/Ascent")
        dsc = _resolve_number(doc, fdx, "FontDescriptor/Descent")
        return DocFont(xref=xref, basefont=basefont, two_byte=two, to_code=to_code,
                       widths=widths, default_width=default_w,
                       ascender=(asc / 1000.0) if asc else 0.9,
                       descender=(dsc / 1000.0) if dsc else -0.2, embedded=embedded)
    except Exception:
        return None


class PageFonts:
    """Шрифты, доступные на конкретном листе (включая формы XObject)."""

    def __init__(self, page: fitz.Page):
        self.page = page
        self.doc = page.parent
        self._by_name: dict[str, list[int]] = {}
        try:
            for f in page.get_fonts(full=True):
                xref, basefont = f[0], f[3]
                self._by_name.setdefault(clean_font_name(basefont), []).append(xref)
        except Exception:
            pass
        self._cache: dict[int, DocFont | None] = {}

    def get(self, xref: int) -> DocFont | None:
        if xref not in self._cache:
            self._cache[xref] = load_docfont(self.doc, xref)
        return self._cache[xref]

    def candidates(self, span_font: str) -> list[DocFont]:
        """Все пригодные шрифты листа с этим именем: встроенные и с бо́льшим набором — первыми."""
        name = clean_font_name(span_font)
        xs = list(self._by_name.get(name, []))
        if not xs:
            for k, v in self._by_name.items():
                if k.split(",")[0] == name or k.replace(" ", "") == name.replace(" ", ""):
                    xs += v
        out = [df for df in (self.get(x) for x in dict.fromkeys(xs)) if df is not None]
        out.sort(key=lambda df: (not df.embedded, -len(df.to_code)))
        return out

    def pick(self, span_font: str, text: str, prefer: "DocFont | None" = None) -> DocFont | None:
        """Шрифт с этим именем, кодирующий весь text (пробелы можно сдвигом)."""
        need = "".join(ch for ch in text if not ch.isspace())
        cands = self.candidates(span_font)
        if prefer is not None and prefer in cands:
            cands.remove(prefer)
            cands.insert(0, prefer)
        for df in cands:
            if df.can_encode(need):
                return df
        return None

    def find(self, span_font: str, sample: str = "") -> DocFont | None:
        """Ищет шрифт листа по имени из извлечённого текста.

        sample — исходные символы этого фрагмента: шрифт обязан их кодировать
        (так отличаем два подмножества одного шрифта).
        """
        name = clean_font_name(span_font)
        cands = list(self._by_name.get(name, []))
        if not cands:
            for k, v in self._by_name.items():
                if k.split(",")[0] == name or k.replace(" ", "") == name.replace(" ", ""):
                    cands += v
        best = None
        for x in cands:
            df = self.get(x)
            if df is None:
                continue
            if sample and not df.can_encode("".join(ch for ch in sample if not ch.isspace())):
                continue
            if best is None or len(df.to_code) > len(best.to_code):
                best = df
        return best
