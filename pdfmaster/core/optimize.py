"""Оптимизация PDF (аналог «Оптимизатора PDF» Acrobat) и аудит занимаемого места."""
from __future__ import annotations

import re

import pymupdf as fitz

PRESETS = {
    "print": {"label": "Высокое качество (печать)", "dpi": 300, "threshold": 450, "quality": 90},
    "standard": {"label": "Стандарт", "dpi": 200, "threshold": 300, "quality": 80},
    "screen": {"label": "Минимальный размер (экран)", "dpi": 150, "threshold": 225, "quality": 65},
}

DEFAULT = {
    "images": True, "dpi": 200, "threshold": 300, "quality": 80, "to_gray": False, "bitonal": True,
    "subset_fonts": True,
    "remove_javascript": True, "remove_thumbnails": True, "remove_metadata": False,
    "remove_attachments": False, "remove_annots": False, "flatten": False, "remove_links": False,
    "remove_hidden_text": False,
    "clean": True,
}


def _xref_kind(doc: fitz.Document, xref: int) -> str:
    try:
        obj = doc.xref_object(xref, compressed=True)
    except Exception:
        return "other"
    if "/Subtype/Image" in obj or "/Subtype /Image" in obj:
        return "images"
    if re.search(r"/Type\s*/XObject", obj) and re.search(r"/Subtype\s*/Form", obj):
        return "forms"
    if re.search(r"/Length1|/Subtype\s*/(Type1C|CIDFontType0C|OpenType)", obj) or "/FontFile" in obj:
        return "fonts"
    if re.search(r"/Type\s*/EmbeddedFile", obj):
        return "attachments"
    if re.search(r"/Type\s*/Metadata", obj):
        return "metadata"
    return "other"


AUDIT_LABELS = {"images": "Картинки", "content": "Содержимое листов (текст, линии)", "forms": "Формы XObject",
                "fonts": "Шрифты", "attachments": "Вложенные файлы", "metadata": "Метаданные",
                "other": "Прочее (структура, закладки, аннотации)"}


def space_audit(doc: fitz.Document, file_size: int | None = None) -> dict:
    """Сколько байт занимает каждая категория (по сжатым потокам)."""
    sizes = {k: 0 for k in AUDIT_LABELS}
    content_xrefs = set()
    for page in doc:
        try:
            content_xrefs.update(page.get_contents())
        except Exception:
            pass
    font_files = set()
    for page in doc:
        try:
            for f in page.get_fonts(full=True):
                font_files.add(f[0])
        except Exception:
            pass
    total_streams = 0
    for xref in range(1, doc.xref_length()):
        try:
            if not doc.xref_is_stream(xref):
                continue
            n = len(doc.xref_stream_raw(xref) or b"")
        except Exception:
            continue
        total_streams += n
        if xref in content_xrefs:
            sizes["content"] += n
        else:
            k = _xref_kind(doc, xref)
            sizes[k] += n
    if file_size:
        sizes["other"] += max(0, file_size - total_streams)
    return sizes


def apply(doc: fitz.Document, opts: dict) -> None:
    """Применяет оптимизацию к открытому документу (до сохранения)."""
    o = {**DEFAULT, **(opts or {})}
    if o.get("flatten"):
        try:
            doc.bake(annots=True, widgets=True)
        except Exception:
            pass
    if o.get("remove_annots"):
        for page in doc:
            for a in list(page.annots() or []):
                try:
                    page.delete_annot(a)
                except Exception:
                    pass
    try:
        doc.scrub(attached_files=bool(o.get("remove_attachments")), clean_pages=False,
                  embedded_files=bool(o.get("remove_attachments")), hidden_text=bool(o.get("remove_hidden_text")),
                  javascript=bool(o.get("remove_javascript")), metadata=bool(o.get("remove_metadata")),
                  redactions=False, remove_links=bool(o.get("remove_links")), reset_fields=False,
                  reset_responses=False, thumbnails=bool(o.get("remove_thumbnails")),
                  xml_metadata=bool(o.get("remove_metadata")))
    except Exception:
        pass
    if o.get("images"):
        thr = max(int(o["threshold"]), int(o["dpi"]) + 1)
        try:
            doc.rewrite_images(dpi_threshold=thr, dpi_target=int(o["dpi"]),
                               quality=int(o["quality"]), lossy=True, lossless=True,
                               bitonal=bool(o.get("bitonal")), color=True, gray=True,
                               set_to_gray=bool(o.get("to_gray")))
        except Exception:
            pass
    if o.get("subset_fonts"):
        try:
            doc.subset_fonts()
        except Exception:
            pass


def save_kwargs(opts: dict) -> dict:
    o = {**DEFAULT, **(opts or {})}
    kw = dict(garbage=4 if o.get("clean") else 3, deflate=True, deflate_images=True, deflate_fonts=True,
              use_objstms=1 if o.get("clean") else 0)
    if o.get("clean"):
        kw["clean"] = True
    return kw
