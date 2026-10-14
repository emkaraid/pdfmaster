"""Правка текста: настоящее удаление старого, новый текст на том же месте."""

import re

import numpy as np
import pymupdf as fitz

from pdfmaster.core import saver
from pdfmaster.core.model import DocModel
from pdfmaster.core.textlines import extract_lines, paragraph_of
from tests.conftest import diff_mask, norm_ws, pdftotext, render


def _lines(m: DocModel, pno: int = 0):
    return extract_lines(m.src_page(m.pages[pno]))


def _edit(m: DocModel, pno: int, find: str, repl: str, style=None):
    pid = m.pages[pno]["id"]
    hits = [ln for ln in _lines(m, pno) if find in ln["text"]]
    assert hits, f"нет строки с «{find}»"
    for ln in hits:
        m.add_obj(pid, {"t": "edit", "lines": [ln], "text": ln["text"].replace(find, repl), "style": style})
    return hits


def _save(m: DocModel, path: str, **opt):
    return saver.save(m.sources, m.job_state(), m.assets, str(path), opt)


def _bbox_mask(shape, rects, dpi=100, pad=3.0):
    mask = np.zeros(shape[:2], bool)
    k = dpi / 72
    for r in rects:
        x0, y0, x1, y1 = r
        mask[max(0, int((y0 - pad) * k)):int((y1 + pad) * k) + 1, max(0, int((x0 - pad) * k)):int((x1 + pad) * k) + 1] = True
    return mask


def test_act_replace_sum(fx, tmp_path):
    """Сценарий 1: акт из Word, 1 250 000 -> 2 480 500."""
    m = DocModel.open(fx["act"])
    hits = _edit(m, 0, "1 250 000", "2 480 500")
    assert len(hits) == 2  # в таблице «Итого» и в тексте
    out = tmp_path / "act_out.pdf"
    _save(m, out)
    txt = pdftotext(str(out))
    assert "1 250 000" not in txt
    assert norm_ws(txt).count("2 480 500") == 2
    # MuPDF-поиск тоже не находит старую сумму
    d = fitz.open(out)
    assert not d[0].search_for("1 250 000")
    assert d[0].search_for("2 480 500")
    # MuPDF может подсветить сумму двумя прямоугольниками (часть цифр — шрифтом-заменой),
    # поэтому вхождения считаем по тексту листа
    assert norm_ws(d[0].get_text()).count("2 480 500") == 2
    # Растровое сравнение: вне правленных строк лист не изменился
    a, b = render(fx["act"]), render(str(out))
    diff = diff_mask(a, b)
    allowed = _bbox_mask(a.shape, [h["bbox"] for h in hits])
    assert diff[~allowed].sum() == 0, f"изменились пиксели вне правки: {diff[~allowed].sum()}"
    assert diff[allowed].sum() > 0  # а в строках — изменились
    # Новый текст на той же базовой линии и того же размера
    for h in hits:
        y = h["chars"][0][2]
        found = [s for bl in d[0].get_text("dict")["blocks"] for ln in bl.get("lines", [])
                 for s in ln["spans"] if "480" in s["text"] and abs(s["origin"][1] - y) < 0.5]
        assert found, "новая сумма не на той же базовой линии"
        assert abs(found[0]["size"] - h["spans"][0]["size"]) < 0.05
        # тот же шрифт, что в исходной строке (свой из документа или тот же по имени)
        from pdfmaster.core.fonts import _core_name
        assert _core_name(found[0]["font"])[0] == _core_name(h["spans"][0]["font"])[0]


def test_unchanged_prefix_keeps_original_glyphs(fx, tmp_path):
    """Начало строки до правки не переписывается (тот же шрифт документа)."""
    m = DocModel.open(fx["act"])
    _edit(m, 0, "Хабибулин И. Р.", "Петров А. А.")
    out = tmp_path / "o.pdf"
    _save(m, out)
    txt = norm_ws(pdftotext(str(out)))
    assert "Подрядчик: ____________ / Петров А. А. /" in txt
    assert "Хабибулин" not in txt


def test_reuse_document_font_when_glyphs_exist(fx, tmp_path):
    """Если символы есть в подмножестве шрифта документа — новый шрифт не встраивается."""
    m = DocModel.open(fx["act"])
    _edit(m, 0, "870 000", "380 000")  # все цифры уже есть в документе
    out = tmp_path / "o.pdf"
    _save(m, out)
    from pdfmaster.core.fonts import clean_font_name
    fonts = {clean_font_name(f[3]) for f in fitz.open(out)[0].get_fonts()}
    before = {clean_font_name(f[3]) for f in fitz.open(fx["act"])[0].get_fonts()}
    assert fonts == before, (fonts, before)  # ни одного нового шрифта — повторно использован шрифт документа


def test_libreoffice_act(fx, tmp_path):
    m = DocModel.open(fx["act_lo"])
    _edit(m, 0, "1 250 000", "2 480 500")
    out = tmp_path / "o.pdf"
    _save(m, out)
    txt = pdftotext(str(out))
    assert "1 250 000" not in txt and "2 480 500" in txt
    assert "руб. 00 коп." in txt


def test_vertical_text_on_drawing(fx, tmp_path):
    """Сценарий 2 (часть): правка вертикальной надписи на чертеже."""
    m = DocModel.open(fx["drawing"])
    hits = _edit(m, 0, "125.40", "126.10")
    assert hits[0]["dir"] == [0.0, -1.0]
    out = tmp_path / "o.pdf"
    _save(m, out)
    txt = pdftotext(str(out))
    assert "125.40" not in txt and "126.10" in txt
    d = fitz.open(out)
    ln = [l for b in d[0].get_text("dict")["blocks"] for l in b.get("lines", [])
          if "126.10" in "".join(s["text"] for s in l["spans"])]
    assert ln and tuple(round(v, 3) for v in ln[0]["dir"]) == (0.0, -1.0)
    # линии чертежа не тронуты
    assert len(d[0].get_cdrawings()) == len(fitz.open(fx["drawing"])[0].get_cdrawings())


def test_gost_substitute_font(fx, tmp_path):
    """Символов нет в подмножестве ГОСТ-шрифта -> встраивается ГОСТ-подобная замена."""
    m = DocModel.open(fx["drawing"])
    _edit(m, 0, "Схема трубопровода", "Профиль водовода Ж-Щ")
    out = tmp_path / "o.pdf"
    _save(m, out)
    d = fitz.open(out)
    spans = [s for b in d[0].get_text("dict")["blocks"] for l in b.get("lines", []) for s in l["spans"]
             if "Профиль" in s["text"]]
    assert spans and "osifont" in spans[0]["font"].lower()
    assert "Схема трубопровода" not in pdftotext(str(out))


def test_style_override(fx, tmp_path):
    m = DocModel.open(fx["act"])
    _edit(m, 0, "Иванову И. И.", "Петрову П. П.",
          style={"family": "orig", "size": 14, "bold": True, "color": "#1a3fa3"})
    out = tmp_path / "o.pdf"
    _save(m, out)
    d = fitz.open(out)
    sp = [s for b in d[0].get_text("dict")["blocks"] for l in b.get("lines", []) for s in l["spans"]
          if "Петрову" in s["text"]][0]
    assert abs(sp["size"] - 14) < 0.05
    assert sp["color"] == 0x1A3FA3
    assert "Bold" in sp["font"] or sp["flags"] & 16


def test_paragraph_reflow(fx, tmp_path):
    m = DocModel.open(fx["act"])
    lines = _lines(m)
    first = [ln for ln in lines if ln["text"].startswith("Мы, нижеподписавшиеся")][0]
    para = paragraph_of(lines, first)
    assert len(para) == 5
    old = " ".join(ln["text"] for ln in para)
    new = old.replace("Насосная станция II подъёма", "Водозабор на р. Енисей (2-я очередь)")
    m.add_obj(m.pages[0]["id"], {"t": "edit", "lines": para, "text": new, "style": None, "para": True})
    out = tmp_path / "o.pdf"
    _save(m, out)
    txt = norm_ws(pdftotext(str(out)))
    assert "Водозабор на р. Енисей (2-я очередь)" in txt
    assert txt.count("Насосная станция") == 0
    # строки не вылезли за правое поле
    d = fitz.open(out)
    right = max(ln["bbox"][2] for ln in para)
    for b in d[0].get_text("dict")["blocks"]:
        for l in b.get("lines", []):
            if l["bbox"][1] > para[0]["bbox"][1] - 1 and l["bbox"][3] < para[-1]["bbox"][3] + 20:
                assert l["bbox"][2] <= right + 1.5


def test_add_multiline_text(fx, tmp_path):
    m = DocModel.open(fx["act"])
    pid = m.pages[0]["id"]
    m.add_obj(pid, {"t": "text", "x": 80, "y": 560, "angle": 0, "text": "Принято без замечаний.\nГл. инженер Петров А. А.",
                    "family": "serif", "size": 12, "bold": False, "italic": False, "color": "#000000"})
    out = tmp_path / "o.pdf"
    _save(m, out)
    txt = pdftotext(str(out))
    assert "Принято без замечаний." in txt and "Гл. инженер Петров А. А." in txt
    d = fitz.open(out)
    a = d[0].search_for("Принято")[0]
    b = d[0].search_for("инженер")[0]
    assert b.y0 > a.y1 - 1  # вторая строка ниже первой


def test_erase_area(fx, tmp_path):
    m = DocModel.open(fx["act"])
    ln = [l for l in _lines(m) if l["text"].startswith("Заказчик")][0]
    m.add_obj(m.pages[0]["id"], {"t": "erase", "rect": ln["bbox"], "white": True})
    out = tmp_path / "o.pdf"
    _save(m, out)
    txt = pdftotext(str(out))
    assert "Заказчик:" not in txt and "Иванов И. И. /" not in txt
    assert "Подрядчик" in txt


def test_delete_text_fragment(fx, tmp_path):
    m = DocModel.open(fx["act"])
    _edit(m, 0, ", НДС не облагается", "")
    out = tmp_path / "o.pdf"
    _save(m, out)
    txt = norm_ws(pdftotext(str(out)))
    assert "НДС" not in txt and "коп." in txt


def test_paragraph_fit_into_original_lines(fx, tmp_path):
    from pdfmaster.core.bake import para_rows
    m = DocModel.open(fx["act"])
    lines = _lines(m)
    first = [ln for ln in lines if ln["text"].startswith("Общая стоимость")][0]
    para = paragraph_of(lines, first)
    assert len(para) == 2
    page = m.src_page(m.pages[0])
    words = " (два миллиона четыреста восемьдесят тысяч пятьсот рублей)"
    base = " ".join(ln["text"] for ln in para)
    new = base.replace("руб. 00", "руб. 00" + words)
    rows, size = para_rows(page, para, new, None)
    if rows <= len(para):  # на другом шрифте могло уместиться — удлиним
        new = base.replace("руб. 00", "руб. 00" + words + " по акту № 15")
        rows, size = para_rows(page, para, new, None)
    assert rows == len(para) + 1                        # без «уместить» — на строку длиннее
    rows, size = para_rows(page, para, new, {"fit": True})
    span_size = para[0]["spans"][para[0]["chars"][0][7]]["size"]
    assert rows == len(para) and size < span_size       # с «уместить» — в прежние строки, кегль меньше
    m.add_obj(m.pages[0]["id"], {"t": "edit", "lines": para, "text": new, "style": {"fit": True}, "para": True})
    out = tmp_path / "o.pdf"
    _save(m, out)
    d = fitz.open(out)
    bottom = max(ln["bbox"][3] for ln in para)
    hits = [l for b in d[0].get_text("dict")["blocks"] for l in b.get("lines", [])
            if "рублей" in "".join(s["text"] for s in l["spans"]) or "облагается" in "".join(s["text"] for s in l["spans"])]
    assert hits and max(l["bbox"][3] for l in hits) <= bottom + 1.0  # не вылезло ниже исходного абзаца


def test_list_item_with_hanging_indent_is_one_paragraph():
    """Пункт «- текст» с продолжением от края колонки — один абзац (лист 56 из ТЗ, 14.10.2026).

    Раньше продолжение, начинающееся на 4+ кегля левее первой строки, в абзац не
    попадало: перенесённый хвост ложился поверх следующей строки."""
    from pdfmaster.core import fonts as F
    d = fitz.open()
    pg = d.new_page(width=595, height=842)
    ff = F.family_file("ptsans", False, False)
    pg.insert_font(fontname="f", fontfile=ff)
    fnt = fitz.Font(fontfile=ff)
    fs, x_text, x_cont, right = 12, 100, 40, 560
    words = ("Проектирование и строительство газопроводов из полиэтиленовых труб и реконструкция "
             "изношенных газопроводов на объектах").split()
    y = 100
    for item in range(2):
        pg.insert_text((80, y), "-", fontname="f", fontsize=fs)
        x, line, first = x_text, [], True
        for w in words:
            wl = fnt.text_length(w + " ", fontsize=fs)
            if x + wl > right:
                pg.insert_text((x_text if first else x_cont, y), " ".join(line), fontname="f", fontsize=fs)
                y += 1.4 * fs
                x, line, first = x_cont, [], False
            line.append(w)
            x += wl
        pg.insert_text((x_text if first else x_cont, y), " ".join(line), fontname="f", fontsize=fs)
        y += 1.4 * fs
    lines = extract_lines(pg)
    heads = [ln for ln in lines if ln["text"].startswith("Проектирование")]
    assert len(heads) == 2
    para = paragraph_of(lines, heads[0])
    assert len(para) >= 2 and para[0] is heads[0]
    assert all(ln is not heads[1] for ln in para), "соседний пункт списка в абзац не входит"
    assert paragraph_of(lines, para[-1])[0] is heads[0], "от продолжения абзац находится вверх до пункта"


def test_type0_font_with_inline_descendant_is_reused(tmp_path):
    """AutoCAD пишет потомка Type0 прямо в массив: /DescendantFonts [<< … >>].
    Такой шрифт документа должен повторно использоваться, а не заменяться."""
    from pdfmaster.core import fonts as F
    src = tmp_path / "src.pdf"
    d = fitz.open()
    pg = d.new_page()
    font = F.family_file("gost", False, False)
    pg.insert_font(fontname="g", fontfile=font)
    pg.insert_text((50, 100), "Проектирование 2004", fontname="g", fontsize=14)
    d.save(src)
    d = fitz.open(src)
    fx = next(f[0] for f in d[0].get_fonts() if f[2] == "Type0")
    obj = d.xref_object(fx)
    m = re.search(r"/DescendantFonts\s*\[\s*(\d+)\s+0\s+R\s*\]", obj)
    assert m
    inline = d.xref_object(int(m.group(1)))
    d.update_object(fx, obj.replace(m.group(0), f"/DescendantFonts [ {inline} ]"))
    out = tmp_path / "inline.pdf"
    d.save(out)
    d = fitz.open(out)
    assert "/DescendantFonts [ <<" in d.xref_object(fx).replace("\n", " ") or "[<<" in d.xref_object(fx)
    df = F.load_docfont(d, fx)
    assert df is not None and df.embedded and df.can_encode("Проектирование2004")
