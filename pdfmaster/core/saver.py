"""Сборка итогового PDF и атомарная запись на диск."""
from __future__ import annotations

import os
import secrets
import tempfile

import pymupdf as fitz

from .bake import BakeContext, bake_page

# Варианты сжатия при сохранении. Нарочно без «агрессивных» режимов:
# ниже 150 dpi / JPEG 75 картинки не опускаем.
COMPRESS = {
    "none": None,
    "gentle": {"dpi_threshold": 260, "dpi_target": 200, "quality": 85, "label": "Бережно (200 dpi, JPEG 85)"},
    "medium": {"dpi_threshold": 190, "dpi_target": 150, "quality": 75, "label": "Средне (150 dpi, JPEG 75)"},
}


def build_document(sources: dict, state: dict, assets: dict, page_ids: list[str] | None = None) -> fitz.Document:
    """Собирает новый fitz.Document по состоянию модели.

    Основа — копия главного исходника: так сохраняются закладки, ссылки,
    метаданные, формы. Листы других исходников вставляются целиком
    (одной операцией на исходник, чтобы шрифты не дублировались), затем
    select() оставляет и упорядочивает нужные.
    """
    main = sources[state["main"]]
    pages = state["pages"]
    if page_ids is not None:
        keep = set(page_ids)
        pages = [p for p in pages if p["id"] in keep]
    if not pages:
        raise ValueError("Нет листов для сохранения")
    out = fitz.open(stream=main.data, filetype="pdf")
    if out.needs_pass:
        out.authenticate(main.password or "")
    offsets: dict[str, int] = {state["main"]: 0}
    used: set[tuple[str, int]] = set()
    order: list[int] = []
    for ref in pages:
        key = (ref["src"], ref["idx"])
        if key in used:
            # тот же лист второй раз — нужна отдельная копия
            out.insert_pdf(sources[ref["src"]].doc, from_page=ref["idx"], to_page=ref["idx"])
            order.append(out.page_count - 1)
            continue
        used.add(key)
        if ref["src"] not in offsets:
            offsets[ref["src"]] = out.page_count
            out.insert_pdf(sources[ref["src"]].doc)
        order.append(offsets[ref["src"]] + ref["idx"])
    if order != list(range(out.page_count)):
        out.select(order)
    ctx = BakeContext(assets=assets)
    objs = state.get("objs", {})
    for i, ref in enumerate(pages):
        page = out[i]
        base = sources[ref["src"]].doc[ref["idx"]].rotation
        rot = (base + ref["rot"]) % 360
        if page.rotation != rot:
            page.set_rotation(rot)
        po = objs.get(ref["id"])
        if po:
            bake_page(page, po, ctx)
    return out


def _uses_substitute_fonts(state: dict, page_ids=None) -> bool:
    for pid, lst in state.get("objs", {}).items():
        if page_ids is not None and pid not in page_ids:
            continue
        for o in lst:
            if o.get("t") in ("edit", "text"):
                return True
    return False


def save_options(main, options: dict) -> dict:
    """Параметры fitz.Document.save()."""
    comp = COMPRESS.get(options.get("compress") or "none")
    kw = dict(garbage=3, deflate=True)
    if comp:
        kw.update(garbage=4, deflate=True, deflate_images=True, deflate_fonts=True,
                  clean=True, use_objstms=1)
    # зашифрованный паролем на открытие файл сохраняем с тем же паролем;
    # ограничения «только чтение» без пароля снимаем (об этом предупреждаем в интерфейсе)
    if getattr(main, "user_password", False) and main.password:
        kw.update(encryption=fitz.PDF_ENCRYPT_AES_256, user_pw=main.password,
                  owner_pw=main.password + secrets.token_hex(4))
    else:
        kw.update(encryption=fitz.PDF_ENCRYPT_NONE)
    return kw


def write_atomic(path: str, writer) -> None:
    """writer(tmp_path) пишет файл; затем он атомарно заменяет path."""
    d = os.path.dirname(os.path.abspath(path)) or "."
    fd, tmp = tempfile.mkstemp(prefix="." + os.path.basename(path) + ".", suffix=".pdfm-tmp", dir=d)
    os.close(fd)
    try:
        writer(tmp)
        with open(tmp, "rb+") as f:
            os.fsync(f.fileno())
        # проверим, что записанное открывается (из памяти: открытый файл Windows не даст удалить)
        with open(tmp, "rb") as f:
            chk = fitz.open(stream=f.read(), filetype="pdf")
        n = chk.page_count
        chk.close()
        if n <= 0:
            raise IOError("записанный файл пуст")
        try:
            st = os.stat(path)
            os.chmod(tmp, st.st_mode & 0o7777)
        except FileNotFoundError:
            os.chmod(tmp, 0o644)
        os.replace(tmp, path)
        try:  # fsync каталога — чтобы переименование пережило сбой питания
            dfd = os.open(d, os.O_RDONLY)
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)
        except OSError:
            pass
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def save(sources: dict, state: dict, assets: dict, path: str, options: dict | None = None,
         page_ids: list[str] | None = None) -> dict:
    """Сохраняет документ в path. Возвращает сведения о результате."""
    options = options or {}
    main = sources[state["main"]]
    pristine = options.get("pristine") and page_ids is None
    comp = COMPRESS.get(options.get("compress") or "none")
    opt = options.get("optimize")
    if pristine and not comp and not opt and not main.restricted:
        # ничего не менялось — пишем исходные байты как есть
        write_atomic(path, lambda tmp: open(tmp, "wb").write(main.data))
        return {"size": len(main.data), "pages": main.page_count}
    doc = build_document(sources, state, assets, page_ids)
    if _uses_substitute_fonts(state, set(page_ids) if page_ids else None):
        try:
            doc.subset_fonts()
        except Exception:
            pass
    if comp:
        try:
            doc.rewrite_images(dpi_threshold=comp["dpi_threshold"], dpi_target=comp["dpi_target"],
                               quality=comp["quality"], lossy=True, lossless=True, bitonal=False)
        except Exception:
            pass
    kw = save_options(main, options)
    if opt:
        from . import optimize
        optimize.apply(doc, opt)
        kw.update(optimize.save_kwargs(opt))
    write_atomic(path, lambda tmp: doc.save(tmp, **kw))
    n = doc.page_count
    doc.close()
    return {"size": os.path.getsize(path), "pages": n}
