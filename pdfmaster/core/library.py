"""Библиотека подписей и печатей: Mac — ~/Library/Application Support/PDF-мастерская/,
Windows — %APPDATA%\\PDF-мастерская\\."""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time

APP_NAME = "PDF-мастерская"


def app_support_dir() -> str:
    d = os.environ.get("PDFM_SUPPORT_DIR")
    if not d:
        if sys.platform.startswith("win"):
            d = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), APP_NAME)
        else:
            d = os.path.expanduser(f"~/Library/Application Support/{APP_NAME}")
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
