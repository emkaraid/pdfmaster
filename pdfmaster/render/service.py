"""Диспетчер процессов-исполнителей (сторона интерфейса).

Задачи ставятся в очередь с приоритетом; задача с тем же ключом заменяет
ещё не начатую (например, рендер листа при быстрой прокрутке). Результаты
приходят сигналом Qt в главный поток.
"""
from __future__ import annotations

import heapq
import itertools
import multiprocessing as mp
import os
import threading

from PySide6.QtCore import QObject, Signal, Slot

from .worker import worker_main


class _Proc:
    def __init__(self, ctx, idx: int, on_result):
        self.idx = idx
        self.conn, child = ctx.Pipe(duplex=True)
        self.proc = ctx.Process(target=worker_main, args=(child,), daemon=True, name=f"pdfm-worker-{idx}")
        self.proc.start()
        child.close()
        self.sids: set[str] = set()
        self.aids: set[str] = set()
        self.busy: dict | None = None
        self.lock = threading.Lock()
        self.thread = threading.Thread(target=self._reader, args=(on_result,), daemon=True)
        self.thread.start()

    def _reader(self, on_result):
        while True:
            try:
                res = self.conn.recv()
            except (EOFError, OSError):
                on_result(self.idx, ("dead", -1))
                return
            on_result(self.idx, res)

    def send(self, msg) -> None:
        with self.lock:
            self.conn.send(msg)


class RenderService(QObject):
    """Результат: done(rid, result_tuple) или failed(rid, message)."""

    done = Signal(int, object)
    failed = Signal(int, str)
    _arrived = Signal(int, object)

    def __init__(self, workers: int | None = None, parent=None):
        super().__init__(parent)
        n = workers or max(2, min(3, (os.cpu_count() or 4) // 3))
        self._ctx = mp.get_context("spawn")
        self._ids = itertools.count(1)
        self._seq = itertools.count()
        self._heap: list = []
        self._by_key: dict = {}
        self._sources: dict[str, tuple] = {}
        self._assets: dict[str, bytes] = {}
        self._cbs: dict[int, tuple] = {}
        self._arrived.connect(self._on_arrived)
        self._procs = [_Proc(self._ctx, i, self._from_thread) for i in range(n)]

    def shutdown(self) -> None:
        for p in self._procs:
            try:
                p.send(None)
            except Exception:
                pass
        for p in self._procs:
            p.proc.join(timeout=1.0)
            if p.proc.is_alive():
                p.proc.terminate()

    # ------------------------------------------------------------ регистрация данных

    def add_source(self, sid: str, name: str, data: bytes, password: str | None) -> None:
        self._sources[sid] = (name, data, password)

    def add_asset(self, aid: str, png: bytes) -> None:
        self._assets[aid] = png

    # ------------------------------------------------------------ задачи

    def submit(self, msg_kind: str, args: tuple, sids=(), aids=(), priority: float = 0.0,
               key=None, callback=None, errback=None, dedicated: bool = False) -> int:
        """Ставит задачу. Меньше priority — раньше. Возвращает rid."""
        rid = next(self._ids)
        job = {"rid": rid, "kind": msg_kind, "args": args, "sids": tuple(sids), "aids": tuple(aids),
               "key": key, "alive": True}
        if key is not None:
            old = self._by_key.get(key)
            if old is not None and old["alive"]:
                old["alive"] = False
                self._cbs.pop(old["rid"], None)
            self._by_key[key] = job
        if callback or errback:
            self._cbs[rid] = (callback, errback)
        heapq.heappush(self._heap, (priority, next(self._seq), job))
        self._pump()
        return rid

    def cancel(self, key) -> None:
        job = self._by_key.pop(key, None)
        if job is not None and job["alive"]:
            job["alive"] = False
            self._cbs.pop(job["rid"], None)

    def cancel_prefix(self, prefix) -> None:
        for k in [k for k in self._by_key if isinstance(k, tuple) and k[:len(prefix)] == prefix]:
            self.cancel(k)

    def _pump(self) -> None:
        for p in self._procs:
            if p.busy is not None:
                continue
            job = None
            while self._heap:
                _, _, j = heapq.heappop(self._heap)
                if j["alive"]:
                    job = j
                    break
            if job is None:
                return
            if job["key"] is not None and self._by_key.get(job["key"]) is job:
                del self._by_key[job["key"]]
            try:
                for sid in job["sids"]:
                    if sid not in p.sids and sid in self._sources:
                        name, data, pw = self._sources[sid]
                        p.send(("src", sid, name, data, pw))
                        p.sids.add(sid)
                for aid in job["aids"]:
                    if aid not in p.aids and aid in self._assets:
                        p.send(("asset", aid, self._assets[aid]))
                        p.aids.add(aid)
                p.busy = job
                p.send((job["kind"], job["rid"]) + tuple(job["args"]))
            except Exception as e:  # процесс умер — перезапустим
                p.busy = None
                self._restart(p)
                self._fail(job["rid"], f"исполнитель недоступен: {e}")

    def _restart(self, p: _Proc) -> None:
        i = self._procs.index(p)
        try:
            p.proc.terminate()
        except Exception:
            pass
        self._procs[i] = _Proc(self._ctx, p.idx, self._from_thread)

    def _from_thread(self, idx: int, res) -> None:
        self._arrived.emit(idx, res)

    @Slot(int, object)
    def _on_arrived(self, idx: int, res) -> None:
        p = next((x for x in self._procs if x.idx == idx), None)
        if res[0] == "dead":
            if p is not None:
                job = p.busy
                p.busy = None
                self._restart(p)
                if job:
                    self._fail(job["rid"], "процесс отрисовки завершился аварийно")
            self._pump()
            return
        if p is not None:
            p.busy = None
        rid = res[1]
        if res[0] == "error":
            self._fail(rid, res[2] + ("\n" + res[3] if len(res) > 3 and os.environ.get("PDFM_DEBUG") else ""))
        else:
            cb = self._cbs.pop(rid, None)
            if cb and cb[0]:
                try:
                    cb[0](res)
                except Exception:
                    import traceback
                    traceback.print_exc()
            self.done.emit(rid, res)
        self._pump()

    def _fail(self, rid: int, msg: str) -> None:
        cb = self._cbs.pop(rid, None)
        if cb and cb[1]:
            try:
                cb[1](msg)
            except Exception:
                pass
        self.failed.emit(rid, msg)
