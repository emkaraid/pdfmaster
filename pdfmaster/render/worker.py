"""Процесс-исполнитель: рендер листов, предпросмотр правок, текст, поиск, сохранение.

MuPDF не отпускает GIL, поэтому тяжёлая работа идёт в отдельных процессах,
а интерфейс остаётся отзывчивым даже на чертежах с сотнями тысяч линий.
"""
from __future__ import annotations

import hashlib
import json
import traceback
from collections import OrderedDict

import pymupdf as fitz

from ..core import saver
from ..core.bake import BakeContext, bake_page
from ..core.model import Source
from ..core.textlines import extract_lines


class _LRU(OrderedDict):
    def __init__(self, cap: int):
        super().__init__()
        self.cap = cap

    def get2(self, k):
        if k in self:
            self.move_to_end(k)
            return self[k]
        return None

    def put(self, k, v):
        self[k] = v
        self.move_to_end(k)
        while len(self) > self.cap:
            self.popitem(last=False)


def _objs_key(objs, skip) -> str:
    return hashlib.sha1(json.dumps([objs, sorted(skip or [])], sort_keys=True, ensure_ascii=False)
                        .encode("utf-8")).hexdigest()


class Worker:
    def __init__(self):
        self.sources: dict[str, Source] = {}
        self.assets: dict[str, bytes] = {}
        self.dls = _LRU(24)      # (sid, idx) -> DisplayList
        self.baked = _LRU(8)     # (sid, idx, key) -> (doc, DisplayList)
        try:
            fitz.TOOLS.set_aa_level(8)
        except Exception:
            pass

    # ------------------------------------------------------------------ helpers

    def _page_dl(self, sid, idx, objs, skip):
        if not objs or all(o.get("id") in (skip or ()) and o.get("t") in ("image", "text") for o in objs):
            k = (sid, idx)
            dl = self.dls.get2(k)
            if dl is None:
                dl = self.sources[sid].doc[idx].get_displaylist()
                self.dls.put(k, dl)
            return dl, self.sources[sid].doc[idx]
        key = (sid, idx, _objs_key(objs, skip))
        hit = self.baked.get2(key)
        if hit is None:
            tmp = fitz.open()
            tmp.insert_pdf(self.sources[sid].doc, from_page=idx, to_page=idx, links=False, annots=True)
            ctx = BakeContext(assets=self.assets)
            bake_page(tmp[0], objs, ctx, skip=set(skip or ()))
            hit = (tmp, tmp[0].get_displaylist())
            self.baked.put(key, hit)
        return hit[1], hit[0][0]

    @staticmethod
    def _matrix(page: fitz.Page, extra_rot: int, scale: float) -> fitz.Matrix:
        """Повёрнутые координаты листа -> пиксели с учётом доп. поворота."""
        R = page.rotation_matrix                      # неповёрнутые -> повёрнутые
        r = page.rect * page.derotation_matrix       # неповёрнутый прямоугольник
        r = fitz.Rect(0, 0, abs(r.width), abs(r.height))
        total = (page.rotation + extra_rot) % 360
        m = fitz.Matrix(1, 1).prerotate(total)
        br = r * m
        D = m * fitz.Matrix(1, 0, 0, 1, -br.x0, -br.y0)  # неповёрнутые -> экран
        return ~R * D * fitz.Matrix(scale, scale)

    # ------------------------------------------------------------------ commands

    def handle(self, msg):
        kind = msg[0]
        if kind == "src":
            _, sid, name, data, password = msg
            if sid not in self.sources:
                self.sources[sid] = Source(sid, name, data, password=password)
            return None
        if kind == "asset":
            _, aid, png = msg
            self.assets[aid] = png
            return None
        if kind == "render":
            _, rid, sid, idx, rot, objs, skip, scale, clip = msg
            dl, page = self._page_dl(sid, idx, objs, skip)
            m = self._matrix(page, rot, scale)
            cl = None
            if clip:
                # clip — в пикселях результата; переводим в координаты списка отображения
                cl = fitz.Rect(clip) * ~m
            pix = dl.get_pixmap(matrix=m, alpha=False, clip=cl)
            return ("img", rid, pix.width, pix.height, pix.x, pix.y, bytes(pix.samples))
        if kind == "lines":
            _, rid, sid, idx = msg[:4]
            objs = msg[4] if len(msg) > 4 else None
            if objs:  # текст листа с учётом правок (для выделения и копирования)
                _, page = self._page_dl(sid, idx, objs, [])
                return ("lines", rid, extract_lines(page))
            return ("lines", rid, extract_lines(self.sources[sid].doc[idx]))
        if kind == "search":
            _, rid, items, query = msg
            res = []
            for pid, sid, idx, objs in items:
                if objs:
                    _, page = self._page_dl(sid, idx, objs, [o["id"] for o in objs if o.get("t") == "image"])
                else:
                    page = self.sources[sid].doc[idx]
                quads = page.search_for(query, quads=True)
                if quads:
                    res.append((pid, [[[q.ul.x, q.ul.y], [q.ur.x, q.ur.y], [q.lr.x, q.lr.y], [q.ll.x, q.ll.y]]
                                      for q in quads]))
            return ("search", rid, res)
        if kind == "save":
            _, rid, state, path, options, page_ids = msg
            info = saver.save(self.sources, state, self.assets, path, options, page_ids=page_ids)
            return ("saved", rid, info)
        if kind == "scale":
            _, rid, sid, idx = msg
            from ..core.measure import text_scales, viewports
            page = self.sources[sid].doc[idx]
            vps = [{"rect": list(v["rect"]), "scale": v["scale"]} for v in viewports(page)]
            return ("scale", rid, vps, text_scales(page))
        if kind == "snap":
            _, rid, sid, idx = msg
            from ..core.measure import snap_points, viewports
            page = self.sources[sid].doc[idx]
            vps = [{"rect": list(v["rect"]), "scale": v["scale"]} for v in viewports(page)]
            return ("snap", rid, snap_points(page), vps)
        if kind == "audit":
            _, rid, sid = msg
            from ..core.optimize import space_audit
            src = self.sources[sid]
            return ("audit", rid, space_audit(src.doc, len(src.data)))
        if kind == "ping":
            return ("pong", msg[1])
        raise ValueError(kind)


def worker_main(conn) -> None:
    w = Worker()
    while True:
        try:
            msg = conn.recv()
        except (EOFError, OSError):
            break
        if msg is None:
            break
        try:
            res = w.handle(msg)
        except Exception as e:  # отдаём ошибку вызывающему
            rid = msg[1] if len(msg) > 1 and isinstance(msg[1], int) else -1
            res = ("error", rid, f"{e}", traceback.format_exc())
        if res is not None:
            try:
                conn.send(res)
            except (BrokenPipeError, OSError):
                break
