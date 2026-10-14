"""Вспомогательные операции: разбор диапазонов листов, разделение документа."""
from __future__ import annotations

import re


def parse_ranges(text: str, count: int) -> list[list[int]]:
    """'1-3, 5; 7-9' -> [[0,1,2],[4],[6,7,8]] (номера с 1, результат с 0).

    Каждая группа через запятую/точку с запятой — отдельная часть.
    'конец' или пустой правый край диапазона означает последний лист.
    """
    parts = []
    for chunk in re.split(r"[;,]", text):
        chunk = chunk.strip().lower()
        if not chunk:
            continue
        m = re.fullmatch(r"(\d+)\s*(?:([-–—])\s*(\d+|конец|end)?)?", chunk)
        if not m:
            raise ValueError(f"Не понимаю диапазон «{chunk}»")
        a = int(m.group(1))
        if m.group(2) is None:
            b = a
        else:
            b = int(m.group(3)) if m.group(3) and m.group(3).isdigit() else count
        if a < 1 or b > count or a > b:
            raise ValueError(f"Диапазон «{chunk}» вне документа (листов: {count})")
        parts.append(list(range(a - 1, b)))
    if not parts:
        raise ValueError("Не указаны листы")
    return parts


def every_n(count: int, n: int) -> list[list[int]]:
    if n < 1:
        raise ValueError("N должно быть больше нуля")
    return [list(range(i, min(count, i + n))) for i in range(0, count, n)]


def part_name(base: str, idxs: list[int]) -> str:
    a, b = idxs[0] + 1, idxs[-1] + 1
    return f"{base}_лист_{a}.pdf" if a == b else f"{base}_листы_{a}-{b}.pdf"


_A = {0: (841, 1189), 1: (594, 841), 2: (420, 594), 3: (297, 420), 4: (210, 297), 5: (148, 210)}


def page_format(w_pt: float, h_pt: float) -> str:
    """Название формата листа: «A4», «A3 альб.», «A4×3» (ГОСТ 2.301), иначе размер в мм."""
    w, h = w_pt * 25.4 / 72, h_pt * 25.4 / 72
    s, l = sorted((w, h))
    land = w > h + 1
    tol = 4.0
    for n, (a_s, a_l) in _A.items():
        if abs(s - a_s) < tol and abs(l - a_l) < tol:
            return f"A{n}" + (" альб." if land else "")
    for n, (a_s, a_l) in _A.items():  # дополнительные форматы: короткая сторона = длинной стороне An
        if abs(s - a_l) < tol:
            k = round(l / a_s)
            if 2 <= k <= 9 and abs(l - k * a_s) < tol * 2:
                return f"A{n}×{k}"
    return f"{round(w)}×{round(h)} мм"
