"""Картинки: загрузка (JPG/PNG/HEIC/PDF), удаление фона у сканов печатей и подписей."""
from __future__ import annotations

import io

import numpy as np
from PIL import Image, ImageFilter, ImageOps

try:  # HEIC со сканами с iPhone
    import pillow_heif

    pillow_heif.register_heif_opener()
except Exception:  # pragma: no cover
    pillow_heif = None

import pymupdf as fitz

MAX_SIDE = 3000  # больше для печати/подписи не нужно (≈ 600 dpi на 12 см)


def open_image(data: bytes) -> Image.Image:
    """Открывает картинку из байтов; PDF — рендер первого листа в 300 dpi."""
    if data[:5] == b"%PDF-" or b"%PDF" in data[:1024]:
        doc = fitz.open(stream=data, filetype="pdf")
        pix = doc[0].get_pixmap(dpi=300, alpha=False)
        img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        img.info["dpi"] = (300, 300)
        return img
    img = Image.open(io.BytesIO(data))
    dpi = img.info.get("dpi")
    img = ImageOps.exif_transpose(img)
    if dpi:
        img.info["dpi"] = dpi
    return img


def image_dpi(img: Image.Image) -> float | None:
    dpi = img.info.get("dpi")
    try:
        v = float(dpi[0]) if dpi else None
    except Exception:
        v = None
    # 72/96 dpi обычно означают «неизвестно»
    if v and 100 <= v <= 2400:
        return v
    return None


def normalize_for_pdf(data: bytes) -> tuple[bytes, int, int, float | None]:
    """Готовит картинку для вставки листом: JPEG оставляем без пересжатия."""
    img = Image.open(io.BytesIO(data))
    fmt = (img.format or "").upper()
    dpi = image_dpi(img)
    orient = 1
    try:
        orient = img.getexif().get(0x0112, 1)
    except Exception:
        pass
    if fmt in ("JPEG", "PNG") and orient in (1, None):
        return data, img.width, img.height, dpi
    img = ImageOps.exif_transpose(img)
    b = io.BytesIO()
    if img.mode in ("RGBA", "LA", "P") and fmt == "PNG":
        img.save(b, "PNG")
    else:
        img.convert("RGB").save(b, "JPEG", quality=93, subsampling=0)
    return b.getvalue(), img.width, img.height, dpi


def _background_map(rgb: np.ndarray) -> np.ndarray:
    """Оценка освещённости фона: «закрытие» по яркости на уменьшенной копии."""
    h, w, _ = rgb.shape
    img = Image.fromarray(rgb.astype(np.uint8))
    k = max(1, int(max(w, h) / 160))
    small = img.resize((max(1, w // k), max(1, h // k)), Image.BILINEAR)
    # MaxFilter убирает тёмные чернила, оставляя фон
    small = small.filter(ImageFilter.MaxFilter(9)).filter(ImageFilter.MaxFilter(5))
    small = small.filter(ImageFilter.GaussianBlur(3))
    bg = np.asarray(small.resize((w, h), Image.BILINEAR)).astype(np.float32)
    return np.maximum(bg, 40.0)


def remove_background(img: Image.Image, threshold: float = 0.16, softness: float = 0.14,
                      despeckle: bool = True) -> Image.Image:
    """Удаляет белый/серый фон скана. Возвращает RGBA, обрезанную по содержимому.

    threshold — насколько пиксель должен отличаться от фона, чтобы считаться
    чернилами (0..1), softness — ширина плавного перехода по краям.
    """
    img = ImageOps.exif_transpose(img) if hasattr(img, "getexif") else img
    if max(img.size) > MAX_SIDE:
        k = MAX_SIDE / max(img.size)
        img = img.resize((round(img.width * k), round(img.height * k)), Image.LANCZOS)
    rgba = np.asarray(img.convert("RGBA")).astype(np.float32)
    rgb = rgba[..., :3]
    src_alpha = rgba[..., 3] / 255.0
    bg = _background_map(rgb)
    norm = np.clip(rgb / bg, 0.0, 1.0)              # фон -> 1.0 (белый)
    ink = 1.0 - norm.min(axis=2)                    # «сила» чернил в любом канале
    a = np.clip((ink - threshold) / max(1e-3, softness), 0.0, 1.0)
    a = a * a * (3 - 2 * a)                         # плавная ступенька
    a *= src_alpha
    if despeckle:
        m = Image.fromarray((a * 255).astype(np.uint8))
        m = m.filter(ImageFilter.MedianFilter(3))
        a = np.asarray(m).astype(np.float32) / 255.0
        a = _drop_small_blobs(a)
    # цвет чернил: «вычитаем» белый фон, чтобы края не были светлыми
    safe = np.maximum(a, 1e-3)[..., None]
    col = (norm - (1.0 - safe)) / safe
    col = np.clip(col, 0.0, 1.0)
    out = np.dstack([col * 255.0, a[..., None] * 255.0]).astype(np.uint8)
    res = Image.fromarray(out, "RGBA")
    return trim(res)


def _drop_small_blobs(a: np.ndarray, min_frac: float = 0.00002) -> np.ndarray:
    """Убирает одиночные крошечные пятна (пыль скана) вдали от основного рисунка."""
    mask = a > 0.06
    total = mask.sum()
    if total == 0:
        return a
    h, w = mask.shape
    # грубая сетка: клетки с малым количеством чернил и без соседей — пыль
    cs = max(4, int(max(h, w) / 120))
    gh, gw = (h + cs - 1) // cs, (w + cs - 1) // cs
    pad = np.zeros((gh * cs, gw * cs), dtype=bool)
    pad[:h, :w] = mask
    cnt = pad.reshape(gh, cs, gw, cs).sum(axis=(1, 3))
    occ = cnt > 0
    nb = np.zeros_like(cnt)
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            if dy == 0 and dx == 0:
                continue
            nb += np.roll(np.roll(occ, dy, 0), dx, 1)
    lonely = occ & (nb == 0) & (cnt < cs * cs * 0.25)
    if not lonely.any():
        return a
    kill = np.repeat(np.repeat(lonely, cs, 0), cs, 1)[:h, :w]
    a = a.copy()
    a[kill] = 0.0
    return a


def trim(img: Image.Image, pad: int = 6, alpha_min: int = 20) -> Image.Image:
    """Обрезает прозрачные поля."""
    a = np.asarray(img.getchannel("A"))
    ys, xs = np.nonzero(a > alpha_min)
    if len(xs) == 0:
        return img
    x0, x1 = max(0, xs.min() - pad), min(img.width, xs.max() + 1 + pad)
    y0, y1 = max(0, ys.min() - pad), min(img.height, ys.max() + 1 + pad)
    return img.crop((x0, y0, x1, y1))


def to_png(img: Image.Image) -> bytes:
    b = io.BytesIO()
    img.save(b, "PNG", optimize=True)
    return b.getvalue()
