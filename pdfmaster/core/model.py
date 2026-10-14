"""Модель документа: источники, список листов, объекты правки, история.

Документ = упорядоченный список ссылок на листы источников (исходный PDF,
добавленные PDF, картинки, пустые листы) + объекты правки на каждом листе.
Исходные байты не меняются до сохранения, поэтому отмена/возврат — это
просто снимки этого небольшого состояния.
"""
from __future__ import annotations

import copy
import hashlib
import io
import itertools
import json
import os

import pymupdf as fitz

_ids = itertools.count(1)


def new_id(prefix: str) -> str:
    return f"{prefix}{next(_ids)}"


class NeedPassword(Exception):
    pass


class WrongPassword(Exception):
    pass


A4 = (595.276, 841.89)


class Source:
    """Неизменяемый исходник: байты PDF и открытый fitz.Document."""

    def __init__(self, sid: str, name: str, data: bytes, path: str | None = None,
                 password: str | None = None, kind: str = "pdf"):
        self.id = sid
        self.name = name
        self.data = data
        self.path = path
        self.kind = kind
        self.password = password
        self.doc = fitz.open(stream=data, filetype="pdf")
        self.encryption = (self.doc.metadata or {}).get("encryption") or ""
        self.user_password = False
        if self.doc.needs_pass:
            if password is None:
                raise NeedPassword(name)
            if not self.doc.authenticate(password):
                raise WrongPassword(name)
            self.user_password = True
        # «защищён от изменений»: зашифрован, но открывается без пароля
        self.restricted = bool(self.encryption) and not self.user_password
        self.signed = False
        try:
            self.signed = bool(self.doc.get_sigflags() > 0)
        except Exception:
            pass

    @property
    def page_count(self) -> int:
        return self.doc.page_count


def image_to_pdf(data: bytes, name: str = "") -> bytes:
    """Картинка (JPG/PNG/HEIC/...) -> одностраничный PDF A4 с картинкой без пересжатия."""
    from . import images
    png_or_jpg, w, h, dpi = images.normalize_for_pdf(data)
    doc = fitz.open()
    land = w > h
    W, H = (A4[1], A4[0]) if land else A4
    m = 14.0
    if dpi:  # известно разрешение — натуральная величина, но не больше листа
        nw, nh = w / dpi * 72, h / dpi * 72
        k = min(1.0, (W - 2 * m) / nw, (H - 2 * m) / nh)
    else:
        nw, nh = w, h
        k = min((W - 2 * m) / nw, (H - 2 * m) / nh)
    iw, ih = nw * k, nh * k
    page = doc.new_page(width=W, height=H)
    r = fitz.Rect((W - iw) / 2, (H - ih) / 2, (W + iw) / 2, (H + ih) / 2)
    page.insert_image(r, stream=png_or_jpg)
    return doc.tobytes(garbage=3, deflate=True)


def blank_pdf(width: float, height: float) -> bytes:
    doc = fitz.open()
    doc.new_page(width=width, height=height)
    return doc.tobytes()


class DocModel:
    def __init__(self):
        self.sources: dict[str, Source] = {}
        self.assets: dict[str, bytes] = {}
        self.main: str | None = None
        self.path: str | None = None
        self.name: str = "Без имени.pdf"
        self.pages: list[dict] = []           # {'id','src','idx','rot'}
        self.objs: dict[str, list[dict]] = {}  # page id -> [obj]
        self._hist: list[str] = []
        self._hi = -1
        self._saved_at = 0
        self._initial: str = ""
        self.listeners = []

    # ------------------------------------------------------------ открытие

    @classmethod
    def open(cls, path: str, password: str | None = None) -> "DocModel":
        m = cls()
        with open(path, "rb") as f:
            data = f.read()
        name = os.path.basename(path)
        if not data.lstrip()[:5].startswith(b"%PDF") and b"%PDF" not in data[:1024]:
            # картинка, открытая как документ
            data = image_to_pdf(data, name)
            name = os.path.splitext(name)[0] + ".pdf"
            path = None
        sid = m.add_source(data, name, path=path, password=password)
        m.main = sid
        m.path = path
        m.name = name
        m.pages = [{"id": new_id("p"), "src": sid, "idx": i, "rot": 0}
                   for i in range(m.sources[sid].page_count)]
        m.objs = {}
        m.reset_history()
        return m

    @classmethod
    def from_bytes(cls, data: bytes, name: str, password: str | None = None) -> "DocModel":
        m = cls()
        sid = m.add_source(data, name, password=password)
        m.main = sid
        m.name = name
        m.pages = [{"id": new_id("p"), "src": sid, "idx": i, "rot": 0}
                   for i in range(m.sources[sid].page_count)]
        m.reset_history()
        return m

    def add_source(self, data: bytes, name: str, path: str | None = None,
                   password: str | None = None, kind: str = "pdf") -> str:
        sid = new_id("s")
        self.sources[sid] = Source(sid, name, data, path=path, password=password, kind=kind)
        return sid

    def add_file_source(self, path: str, password: str | None = None) -> str:
        with open(path, "rb") as f:
            data = f.read()
        name = os.path.basename(path)
        if b"%PDF" in data[:1024]:
            return self.add_source(data, name, path=path, password=password)
        return self.add_source(image_to_pdf(data, name), name, kind="image")

    def add_asset(self, png: bytes) -> str:
        aid = "a" + hashlib.sha1(png).hexdigest()[:16]
        self.assets.setdefault(aid, png)
        return aid

    # ------------------------------------------------------------ доступ

    @property
    def main_source(self) -> Source:
        return self.sources[self.main]

    def page_index(self, pid: str) -> int:
        for i, p in enumerate(self.pages):
            if p["id"] == pid:
                return i
        return -1

    def page_ref(self, pid: str) -> dict | None:
        i = self.page_index(pid)
        return self.pages[i] if i >= 0 else None

    def src_page(self, ref: dict) -> fitz.Page:
        return self.sources[ref["src"]].doc[ref["idx"]]

    def total_rotation(self, ref: dict) -> int:
        return (self.src_page(ref).rotation + ref["rot"]) % 360

    def unrotated_rect(self, ref: dict) -> fitz.Rect:
        """Прямоугольник неповёрнутого листа в координатах MuPDF."""
        pg = self.src_page(ref)
        r = pg.rect * pg.derotation_matrix
        return fitz.Rect(0, 0, abs(r.width), abs(r.height))

    def display_matrix(self, ref: dict) -> fitz.Matrix:
        """Неповёрнутые координаты листа -> координаты экрана (пункты, 0,0 сверху слева)."""
        r = self.unrotated_rect(ref)
        rot = self.total_rotation(ref)
        m = fitz.Matrix(1, 1).prerotate(rot)
        br = r * m
        return m * fitz.Matrix(1, 0, 0, 1, -br.x0, -br.y0)

    def display_size(self, ref: dict) -> tuple[float, float]:
        r = self.unrotated_rect(ref)
        if self.total_rotation(ref) in (90, 270):
            return r.height, r.width
        return r.width, r.height

    def to_unrotated(self, ref: dict, x: float, y: float) -> tuple[float, float]:
        """Точка на экране (пункты листа как он виден) -> неповёрнутые координаты."""
        p = fitz.Point(x, y) * ~self.display_matrix(ref)
        return p.x, p.y

    def to_display(self, ref: dict, x: float, y: float) -> tuple[float, float]:
        p = fitz.Point(x, y) * self.display_matrix(ref)
        return p.x, p.y

    def upright_angle(self, ref: dict) -> float:
        """Угол (в неповёрнутых координатах), при котором объект виден ровно."""
        return float((-self.total_rotation(ref)) % 360)

    def page_objs(self, pid: str) -> list[dict]:
        return self.objs.get(pid, [])

    def find_obj(self, oid: str) -> tuple[str, dict] | tuple[None, None]:
        for pid, lst in self.objs.items():
            for o in lst:
                if o.get("id") == oid:
                    return pid, o
        return None, None

    # ------------------------------------------------------------ история

    def _snap(self) -> str:
        return json.dumps({"pages": self.pages, "objs": self.objs}, ensure_ascii=False)

    def reset_history(self) -> None:
        s = self._snap()
        self._hist = [s]
        self._hi = 0
        self._saved_at = 0
        self._initial = s

    def commit(self) -> None:
        s = self._snap()
        if self._hist and self._hist[self._hi] == s:
            return
        self._hist = self._hist[: self._hi + 1]
        self._hist.append(s)
        self._hi += 1
        if len(self._hist) > 300:
            drop = len(self._hist) - 300
            self._hist = self._hist[drop:]
            self._hi -= drop
            self._saved_at -= drop
        self._notify()

    def _load(self, s: str) -> None:
        st = json.loads(s)
        self.pages = st["pages"]
        self.objs = st["objs"]

    def can_undo(self) -> bool:
        return self._hi > 0

    def can_redo(self) -> bool:
        return self._hi < len(self._hist) - 1

    def undo(self) -> bool:
        if not self.can_undo():
            return False
        self._hi -= 1
        self._load(self._hist[self._hi])
        self._notify()
        return True

    def redo(self) -> bool:
        if not self.can_redo():
            return False
        self._hi += 1
        self._load(self._hist[self._hi])
        self._notify()
        return True

    @property
    def dirty(self) -> bool:
        return self._hi != self._saved_at or self.main is None

    def mark_saved(self) -> None:
        self._saved_at = self._hi
        self._notify()

    def is_pristine(self) -> bool:
        """Состояние совпадает с исходным файлом (ничего не изменено)."""
        return self._snap() == self._initial

    def _notify(self) -> None:
        for cb in list(self.listeners):
            try:
                cb()
            except Exception:
                pass

    # ------------------------------------------------------------ операции с листами

    def rotate_pages(self, pids, delta: int = 90) -> None:
        for p in self.pages:
            if p["id"] in pids:
                p["rot"] = (p["rot"] + delta) % 360
        self.commit()

    def delete_pages(self, pids) -> None:
        pids = set(pids)
        self.pages = [p for p in self.pages if p["id"] not in pids]
        for pid in pids:
            self.objs.pop(pid, None)
        self.commit()

    def move_pages(self, pids: list[str], before: int) -> None:
        """Переносит листы pids так, чтобы они стояли перед позицией before."""
        sel = [p for p in self.pages if p["id"] in pids]
        if not sel:
            return
        k = sum(1 for p in self.pages[:before] if p["id"] in pids)
        rest = [p for p in self.pages if p["id"] not in pids]
        at = max(0, min(len(rest), before - k))
        self.pages = rest[:at] + sel + rest[at:]
        self.commit()

    def set_order(self, pids: list[str]) -> None:
        by = {p["id"]: p for p in self.pages}
        new = [by[i] for i in pids if i in by]
        if len(new) == len(self.pages):
            self.pages = new
            self.commit()

    def insert_source_pages(self, sid: str, at: int, indices=None, commit: bool = True) -> list[str]:
        src = self.sources[sid]
        idxs = list(range(src.page_count)) if indices is None else list(indices)
        new = [{"id": new_id("p"), "src": sid, "idx": i, "rot": 0} for i in idxs]
        at = max(0, min(len(self.pages), at))
        self.pages[at:at] = new
        if commit:
            self.commit()
        return [p["id"] for p in new]

    def insert_blank(self, at: int, like_pid: str | None = None) -> str:
        w, h = A4
        ref = self.page_ref(like_pid) if like_pid else (self.pages[min(at, len(self.pages) - 1)] if self.pages else None)
        if ref:
            w, h = self.display_size(ref)
        sid = self.add_source(blank_pdf(w, h), "Пустой лист", kind="blank")
        return self.insert_source_pages(sid, at)[0]

    # ------------------------------------------------------------ объекты

    def add_obj(self, pid: str, obj: dict, commit: bool = True) -> dict:
        obj.setdefault("id", new_id("o"))
        self.objs.setdefault(pid, []).append(obj)
        if commit:
            self.commit()
        return obj

    def update_obj(self, oid: str, commit: bool = True, **changes) -> dict | None:
        pid, o = self.find_obj(oid)
        if o is None:
            return None
        o.update(changes)
        if commit:
            self.commit()
        return o

    def remove_obj(self, oid: str, commit: bool = True) -> None:
        for pid, lst in self.objs.items():
            for i, o in enumerate(lst):
                if o.get("id") == oid:
                    del lst[i]
                    if commit:
                        self.commit()
                    return

    def edit_for_line(self, pid: str, line_key: str) -> dict | None:
        """Существующая правка, в которую входит строка с данным ключом."""
        from .textlines import line_key as lk
        for o in self.objs.get(pid, []):
            if o.get("t") == "edit" and any(lk(ln) == line_key for ln in o["lines"]):
                return o
        return None

    # ------------------------------------------------------------ сериализация для процессов

    def job_state(self) -> dict:
        return {"main": self.main, "pages": copy.deepcopy(self.pages), "objs": copy.deepcopy(self.objs)}


def dump_png(img) -> bytes:
    b = io.BytesIO()
    img.save(b, format="PNG", optimize=False)
    return b.getvalue()
