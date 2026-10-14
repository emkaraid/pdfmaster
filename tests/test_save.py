"""Сценарии 6 и 7: защищённый файл; атомарное сохранение; отмена до исходного."""
import hashlib
import os
import shutil

import numpy as np
import pymupdf as fitz
import pytest

from pdfmaster.core import saver
from pdfmaster.core.model import DocModel
from pdfmaster.core.textlines import extract_lines
from tests.conftest import pdftotext, render


def _edit_sum(m):
    pid = m.pages[0]["id"]
    for ln in extract_lines(m.src_page(m.pages[0])):
        if "1 250 000" in ln["text"]:
            m.add_obj(pid, {"t": "edit", "lines": [ln], "text": ln["text"].replace("1 250 000", "2 480 500"),
                            "style": None})


def _sha(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()


def test_protected_open_edit_save(fx, tmp_path):
    p = tmp_path / "prot.pdf"
    shutil.copy(fx["protected"], p)
    assert fitz.open(p).metadata["encryption"]
    assert not fitz.open(p).permissions & fitz.PDF_PERM_MODIFY
    m = DocModel.open(str(p))
    assert m.main_source.restricted          # интерфейс предупредит о снятии ограничений
    assert not m.main_source.user_password
    _edit_sum(m)
    saver.save(m.sources, m.job_state(), m.assets, str(p))
    d = fitz.open(p)
    assert not d.needs_pass and not d.metadata.get("encryption")
    txt = pdftotext(str(p))
    assert "2 480 500" in txt and "1 250 000" not in txt


def test_user_password_kept(fx, tmp_path):
    src = tmp_path / "pw.pdf"
    fitz.open(fx["act"]).save(src, encryption=fitz.PDF_ENCRYPT_AES_256, user_pw="1234", owner_pw="x")
    with pytest.raises(Exception):
        DocModel.open(str(src))
    m = DocModel.open(str(src), password="1234")
    _edit_sum(m)
    saver.save(m.sources, m.job_state(), m.assets, str(src))
    d = fitz.open(src)
    assert d.needs_pass and d.authenticate("1234")
    assert "2 480 500" in d[0].get_text()


def test_atomic_save_failure_keeps_original(fx, tmp_path):
    p = tmp_path / "act.pdf"
    shutil.copy(fx["act"], p)
    before = _sha(p)

    def bad_writer(tmp):
        with open(tmp, "wb") as f:
            f.write(b"%PDF-1.7\n broken")
        raise RuntimeError("сбой посреди записи")

    with pytest.raises(RuntimeError):
        saver.write_atomic(str(p), bad_writer)
    assert _sha(p) == before
    assert os.listdir(tmp_path) == ["act.pdf"]  # временный файл убран


def test_atomic_save_rejects_corrupt_output(fx, tmp_path):
    p = tmp_path / "act.pdf"
    shutil.copy(fx["act"], p)
    before = _sha(p)
    with pytest.raises(Exception):
        saver.write_atomic(str(p), lambda tmp: open(tmp, "wb").write(b"not a pdf"))
    assert _sha(p) == before
    assert os.listdir(tmp_path) == ["act.pdf"]


def test_save_over_original_uses_temp_and_replace(fx, tmp_path, monkeypatch):
    p = tmp_path / "act.pdf"
    shutil.copy(fx["act"], p)
    ino_before = os.stat(p).st_ino
    calls = []
    real_replace = os.replace

    def spy(a, b):
        calls.append((os.path.dirname(a), os.path.basename(a), b))
        return real_replace(a, b)

    monkeypatch.setattr(os, "replace", spy)
    m = DocModel.open(str(p))
    _edit_sum(m)
    saver.save(m.sources, m.job_state(), m.assets, str(p))
    assert calls and calls[0][0] == str(tmp_path) and calls[0][2] == str(p)
    assert calls[0][1].endswith(".pdfm-tmp")
    assert os.stat(p).st_ino != ino_before  # файл заменён целиком, а не дописан
    assert "2 480 500" in pdftotext(str(p))


def test_undo_to_original_saves_identical_content(fx, tmp_path):
    p = tmp_path / "act.pdf"
    shutil.copy(fx["act"], p)
    orig_sha = _sha(p)
    m = DocModel.open(str(p))
    _edit_sum(m)
    m.rotate_pages([m.pages[0]["id"]])
    m.add_obj(m.pages[0]["id"], {"t": "text", "x": 50, "y": 50, "angle": 0, "text": "временно",
                                 "family": "serif", "size": 12, "color": "#000000"})
    while m.can_undo():
        m.undo()
    assert m.is_pristine()
    # путь программы: состояние исходное -> файл совпадает побайтно
    saver.save(m.sources, m.job_state(), m.assets, str(p), {"pristine": m.is_pristine()})
    assert _sha(p) == orig_sha
    # и даже при принудительной пересборке совпадает содержимое: текст и картинка листа
    out = tmp_path / "rebuilt.pdf"
    saver.save(m.sources, m.job_state(), m.assets, str(out), {"pristine": False})
    assert pdftotext(str(out)) == pdftotext(str(p))
    a, b = render(str(p)), render(str(out))
    assert np.abs(a - b).max() == 0
    assert fitz.open(out).page_count == fitz.open(p).page_count


def test_compress_option(fx, tmp_path):
    # документ со сканом высокого разрешения
    m = DocModel.open(fx["act"])
    from PIL import Image
    big = tmp_path / "scan.png"
    Image.open(fx["stamp"]).resize((2400, 2400)).save(big)
    sid = m.add_file_source(str(big))
    m.insert_source_pages(sid, 1)
    plain = tmp_path / "plain.pdf"
    comp = tmp_path / "comp.pdf"
    saver.save(m.sources, m.job_state(), m.assets, str(plain))
    saver.save(m.sources, m.job_state(), m.assets, str(comp), {"compress": "gentle"})
    assert os.path.getsize(comp) < os.path.getsize(plain)
    assert pdftotext(str(comp)) == pdftotext(str(plain))
    # разрешение картинки не ниже 200 dpi
    d = fitz.open(comp)
    info = d[1].get_image_info()[0]
    w_in = (info["bbox"][2] - info["bbox"][0]) / 72
    assert info["width"] / w_in >= 190


def test_optimize_like_acrobat(fx, tmp_path):
    from pdfmaster.core import optimize
    m = DocModel.open(fx["act"])
    from PIL import Image
    big = tmp_path / "scan.png"
    Image.open(fx["stamp"]).resize((2400, 2400)).save(big)
    sid = m.add_file_source(str(big))
    m.insert_source_pages(sid, 1)
    plain = tmp_path / "plain.pdf"
    saver.save(m.sources, m.job_state(), m.assets, str(plain))
    audit = optimize.space_audit(fitz.open(plain), os.path.getsize(plain))
    assert audit["images"] > audit["fonts"] > 0 and sum(audit.values()) >= os.path.getsize(plain) * 0.95
    sizes = {}
    for name, pre in optimize.PRESETS.items():
        out = tmp_path / f"{name}.pdf"
        opts = {**optimize.DEFAULT, "dpi": pre["dpi"], "threshold": pre["threshold"], "quality": pre["quality"],
                "remove_metadata": True}
        saver.save(m.sources, m.job_state(), m.assets, str(out), {"optimize": opts})
        sizes[name] = os.path.getsize(out)
        assert pdftotext(str(out)) == pdftotext(str(plain))  # текст не пострадал
    assert sizes["screen"] < sizes["standard"] <= sizes["print"] < os.path.getsize(plain)
    # в оттенки серого
    out = tmp_path / "gray.pdf"
    saver.save(m.sources, m.job_state(), m.assets, str(out),
               {"optimize": {**optimize.DEFAULT, "to_gray": True, "threshold": 200, "dpi": 150}})
    pix = fitz.Pixmap(fitz.open(out), fitz.open(out)[1].get_images()[0][0])
    assert pix.n - pix.alpha == 1 and pix.width < 1500
