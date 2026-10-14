"""Библиотека подписей и печатей: Mac — ~/Library/Application Support/PDFMaster/,
Windows — %APPDATA%\\PDFMaster\\."""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time

APP_NAME = "PDFMaster"
OLD_NAME = "PDF-мастерская"   # название папки до версии 1.2


def app_support_dir() -> str:
    d = os.environ.get("PDFM_SUPPORT_DIR")
    if not d:
        if sys.platform.startswith("win"):
            base = os.environ.get("APPDATA") or os.path.expanduser("~")
        else:
            base = os.path.expanduser("~/Library/Application Support")
        d = os.path.join(base, APP_NAME)
        old = os.path.join(base, OLD_NAME)
        if not os.path.exists(d) and os.path.isdir(old):
            try:  # подписи и печати из прежней версии переезжают в новую папку
                os.rename(old, d)
            except OSError:
                d = old
    os.makedirs(d, exist_ok=True)
    return d


class Library:
    def __init__(self, root: str | None = None):
        self.root = os.path.join(root or app_support_dir(), "library")
        os.makedirs(self.root, exist_ok=True)
        self.index_path = os.path.join(self.root, "index.json")
        self._items: list[dict] = []
        self.load()

    def load(self) -> None:
        try:
            with open(self.index_path, encoding="utf-8") as f:
                self._items = [it for it in json.load(f)
                               if os.path.exists(os.path.join(self.root, it["file"]))]
        except (OSError, ValueError):
            self._items = []

    def _save(self) -> None:
        tmp = self.index_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._items, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.index_path)

    def items(self) -> list[dict]:
        return list(self._items)

    def add(self, png: bytes, kind: str = "stamp", name: str = "", width_pt: float | None = None) -> dict:
        h = hashlib.sha1(png).hexdigest()[:16]
        for it in self._items:
            if it["id"] == h:
                return it
        fn = f"{h}.png"
        with open(os.path.join(self.root, fn), "wb") as f:
            f.write(png)
        it = {"id": h, "file": fn, "kind": kind, "name": name or ("Подпись" if kind == "signature" else "Печать"),
              "width_pt": width_pt, "created": time.time()}
        self._items.insert(0, it)
        self._save()
        return it

    def update(self, iid: str, **kw) -> None:
        for it in self._items:
            if it["id"] == iid:
                it.update(kw)
        self._save()

    def remove(self, iid: str) -> None:
        for it in list(self._items):
            if it["id"] == iid:
                self._items.remove(it)
                try:
                    os.unlink(os.path.join(self.root, it["file"]))
                except OSError:
                    pass
        self._save()

    def png(self, iid: str) -> bytes:
        for it in self._items:
            if it["id"] == iid:
                with open(os.path.join(self.root, it["file"]), "rb") as f:
                    return f.read()
        raise KeyError(iid)

    def path(self, iid: str) -> str:
        for it in self._items:
            if it["id"] == iid:
                return os.path.join(self.root, it["file"])
        raise KeyError(iid)

    # ------------------------------------------------------------ поворот, перенос на другой компьютер

    def rotate(self, iid: str, deg: int) -> None:
        """Поворачивает образец (по часовой при deg > 0); размер на листе пересчитывается."""
        from PIL import Image
        for it in self._items:
            if it["id"] != iid:
                continue
            p = os.path.join(self.root, it["file"])
            im = Image.open(p).convert("RGBA")
            w0 = im.width
            r = im.rotate(-deg, expand=True)
            r.save(p, "PNG", optimize=True)
            if it.get("width_pt") and deg % 180:
                it["width_pt"] = it["width_pt"] * r.width / w0
        self._save()

    def export_zip(self, path: str) -> int:
        """Все образцы в один файл .zip (для переноса на другой компьютер)."""
        import zipfile
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("index.json", json.dumps(self._items, ensure_ascii=False, indent=1))
            for it in self._items:
                z.write(os.path.join(self.root, it["file"]), it["file"])
        return len(self._items)

    def import_zip(self, path: str) -> int:
        """Добавляет образцы из файла экспорта; одинаковые не дублируются."""
        import zipfile
        n = 0
        with zipfile.ZipFile(path) as z:
            items = json.loads(z.read("index.json").decode("utf-8"))
            for it in reversed(items):
                png = z.read(it["file"])
                before = len(self._items)
                self.add(png, it.get("kind", "stamp"), name=it.get("name", ""), width_pt=it.get("width_pt"))
                n += len(self._items) - before
        return n
