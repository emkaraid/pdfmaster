"""Сценарий 4: перестановка, поворот, удаление, вставка и извлечение листов."""
import os

import pymupdf as fitz
import pytest

from pdfmaster.core import ops, saver
from pdfmaster.core.model import DocModel


def labels(path_or_doc):
    d = fitz.open(path_or_doc) if isinstance(path_or_doc, str) else path_or_doc
    out = []
    for p in d:
        t = " ".join(p.get_text().split())
        out.append((t.split("Лист ")[-1].split()[0] if "Лист " in t else ("пусто" if not t else t[:12]), p.rotation))
    return out


def test_reorder_rotate_delete_insert(fx, tmp_path):
    m = DocModel.open(fx["pages"])
    ids = [p["id"] for p in m.pages]
    assert labels(fx["pages"]) == [("1", 0), ("2", 0), ("3", 90), ("4", 0), ("5", 0), ("6", 0)]
    m.rotate_pages([ids[0]], 90)              # 1 -> 90°
    m.rotate_pages([ids[2]], 90)              # 3: было 90 -> 180
    m.move_pages([ids[4]], 0)                 # 5 в начало
    m.delete_pages([ids[1]])                  # убрать 2
    blank = m.insert_blank(2)                 # пустой лист третьим
    sid = m.add_file_source(fx["act"])
    m.insert_source_pages(sid, len(m.pages))  # акт в конец
    out = tmp_path / "o.pdf"
    saver.save(m.sources, m.job_state(), m.assets, str(out))
    got = labels(str(out))
    assert [g[0] for g in got] == ["5", "1", "пусто", "3", "4", "6", "Директору ОО"]
    assert [g[1] for g in got] == [0, 90, 0, 180, 0, 0, 0]
    # размеры: 4-й лист альбомный остался альбомным
    d = fitz.open(out)
    assert d[4].rect.width > d[4].rect.height
    # пустой лист того же размера, что соседний (как он виден)
    assert abs(d[2].rect.width - d[1].rect.width) < 1 or abs(d[2].rect.width - d[3].rect.width) < 1
    # закладки сохранились и указывают на правильные листы
    toc = d.get_toc()
    assert ["Третий", 4] in [[t[1], t[2]] for t in toc]
    assert blank


def test_undo_redo_page_ops(fx):
    m = DocModel.open(fx["pages"])
    ids = [p["id"] for p in m.pages]
    m.move_pages([ids[5]], 0)
    m.rotate_pages([ids[0]])
    m.delete_pages([ids[3]])
    assert [p["id"] for p in m.pages] == [ids[5], ids[0], ids[1], ids[2], ids[4]]
    m.undo()
    m.undo()
    assert [p["rot"] for p in m.pages] == [0] * 6
    m.redo()
    assert m.pages[1]["rot"] == 90
    m.undo()
    m.undo()
    assert [p["id"] for p in m.pages] == ids
    assert not m.can_undo()
    assert m.is_pristine()


def test_extract_selected(fx, tmp_path):
    m = DocModel.open(fx["pages"])
    sel = [m.pages[i]["id"] for i in (1, 3, 4)]
    out = tmp_path / "part.pdf"
    saver.save(m.sources, m.job_state(), m.assets, str(out), page_ids=sel)
    assert [g[0] for g in labels(str(out))] == ["2", "4", "5"]
    # исходная модель не изменилась
    assert len(m.pages) == 6


def test_split_ranges_and_every_n(fx, tmp_path):
    m = DocModel.open(fx["pages"])
    parts = ops.parse_ranges("1-2, 3; 4-конец", len(m.pages))
    assert parts == [[0, 1], [2], [3, 4, 5]]
    for k, idxs in enumerate(parts):
        out = tmp_path / ops.part_name("pages6", idxs)
        saver.save(m.sources, m.job_state(), m.assets, str(out), page_ids=[m.pages[i]["id"] for i in idxs])
    names = sorted(os.listdir(tmp_path))
    assert "pages6_листы_1-2.pdf" in names and "pages6_лист_3.pdf" in names
    assert [g[0] for g in labels(str(tmp_path / "pages6_листы_4-6.pdf"))] == ["4", "5", "6"]
    assert ops.every_n(7, 3) == [[0, 1, 2], [3, 4, 5], [6]]
    with pytest.raises(ValueError):
        ops.parse_ranges("5-9", 6)


def test_merge_files_and_images(fx, tmp_path):
    m = DocModel.open(fx["act"])
    s1 = m.add_file_source(fx["pages"])
    m.insert_source_pages(s1, 1, indices=[0, 1])
    s2 = m.add_file_source(fx["stamp"])    # картинка JPG -> лист
    m.insert_source_pages(s2, 0)
    out = tmp_path / "merged.pdf"
    saver.save(m.sources, m.job_state(), m.assets, str(out))
    d = fitz.open(out)
    assert d.page_count == 4
    assert d[0].get_images()  # скан
    assert "Директору" in d[1].get_text()
    assert "Лист 1" in d[2].get_text() and "Лист 2" in d[3].get_text()
    # JPEG вставлен без пересжатия
    xref = d[0].get_images()[0][0]
    assert d.extract_image(xref)["ext"] in ("jpeg", "jpg")


def test_heic_image_page(fx, tmp_path):
    pillow_heif = pytest.importorskip("pillow_heif")
    from PIL import Image
    p = tmp_path / "scan.heic"
    Image.open(fx["stamp"]).save(p, format="HEIF")
    m = DocModel.open(fx["act"])
    sid = m.add_file_source(str(p))
    m.insert_source_pages(sid, 1)
    out = tmp_path / "o.pdf"
    saver.save(m.sources, m.job_state(), m.assets, str(out))
    assert fitz.open(out).page_count == 2
    assert pillow_heif
