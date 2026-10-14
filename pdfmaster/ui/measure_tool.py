"""Инструмент «Линейка»: расстояние, ломаная (длина), площадь — с привязкой к концам линий
и масштабом 1:N (из файла AutoCAD/nanoCAD, выбранным вручную или по калибровке)."""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainterPath, QPen
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QGraphicsEllipseItem, QGraphicsItem,
                               QGraphicsPathItem, QGraphicsRectItem, QGraphicsSimpleTextItem, QHBoxLayout,
                               QInputDialog, QLabel, QListWidget, QPushButton, QRadioButton, QVBoxLayout, QWidget)

from ..core import measure as M

COLOR = QColor(214, 69, 47)
SCALES = [0, 1, 50, 100, 200, 250, 500, 1000, 2000, 5000, 10000]


class MeasurePanel(QWidget):
    changed = Signal()
    calibrate = Signal()
    toDocument = Signal()
    clearAll = Signal()

    def __init__(self):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        t = QLabel("Линейка")
        f = t.font()
        f.setBold(True)
        f.setPointSizeF(f.pointSizeF() + 1)
        t.setFont(f)
        lay.addWidget(t)
        hint = QLabel("Кликните в начало и в конец. Для ломаной и площади — несколько кликов, двойной клик или "
                      "Enter — готово, Esc — отмена. ⇧ — угол кратно 15°.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: palette(mid); font-size: 11px;")
        lay.addWidget(hint)
        self.kind = QButtonGroup(self)
        row = QHBoxLayout()
        for i, (k, name) in enumerate((("dist", "Расстояние"), ("poly", "Ломаная"), ("area", "Площадь"))):
            rb = QRadioButton(name)
            rb.setProperty("k", k)
            self.kind.addButton(rb, i)
            row.addWidget(rb)
            if i == 0:
                rb.setChecked(True)
        lay.addLayout(row)
        r2 = QHBoxLayout()
        r2.addWidget(QLabel("Масштаб:"))
        self.scale = QComboBox()
        self.scale.addItem("авто (из файла)", 0)
        self.scale.addItem("1:1 — на листе, мм", 1)
        for n in SCALES[2:]:
            self.scale.addItem(f"1:{n}", n)
        self.scale.addItem("свой…", -1)
        r2.addWidget(self.scale, 1)
        lay.addLayout(r2)
        self.file_scale = QLabel("")
        self.file_scale.setStyleSheet("color: palette(mid); font-size: 11px;")
        self.file_scale.setWordWrap(True)
        lay.addWidget(self.file_scale)
        cal = QPushButton("Откалибровать по известному отрезку…")
        cal.setToolTip("Измерьте отрезок известной длины (например, размер на плане) и введите его длину")
        cal.clicked.connect(self.calibrate.emit)
        lay.addWidget(cal)
        self.snap = QCheckBox("Привязка к концам линий")
        self.snap.setChecked(True)
        lay.addWidget(self.snap)
        self.result = QLabel("—")
        f2 = self.result.font()
        f2.setPointSizeF(f2.pointSizeF() + 5)
        f2.setBold(True)
        self.result.setFont(f2)
        self.result.setWordWrap(True)
        self.result.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(self.result)
        self.sub = QLabel("")
        self.sub.setStyleSheet("color: palette(mid);")
        self.sub.setWordWrap(True)
        lay.addWidget(self.sub)
        lay.addWidget(QLabel("Измерения:"))
        self.list = QListWidget()
        self.list.setMinimumHeight(120)
        self.list.setWordWrap(True)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        lay.addWidget(self.list, 1)
        r3 = QHBoxLayout()
        doc = QPushButton("Нанести в документ")
        doc.setToolTip("Записать измерения в PDF размерными линиями с подписями")
        doc.clicked.connect(self.toDocument.emit)
        clr = QPushButton("Очистить")
        clr.clicked.connect(self.clearAll.emit)
        r3.addWidget(doc)
        r3.addWidget(clr)
        lay.addLayout(r3)
        self.kind.idToggled.connect(lambda *_: self.changed.emit())
        self.scale.currentIndexChanged.connect(self._scale_changed)
        self._custom = None

    def kind_key(self) -> str:
        b = self.kind.checkedButton()
        return b.property("k") if b else "dist"

    def _scale_changed(self, *_):
        if self.scale.currentData() == -1:
            n, ok = QInputDialog.getDouble(self, "Масштаб", "Масштаб 1 :", self._custom or 500, 0.01, 1e6, 2)
            if ok:
                self.set_custom(n)
            else:
                self.scale.setCurrentIndex(0)
        self.changed.emit()

    def set_custom(self, n: float) -> None:
        self._custom = n
        i = self.scale.findData(-2)
        label = f"1:{n:g} (свой)"
        if i < 0:
            self.scale.insertItem(self.scale.count() - 1, label, -2)
            i = self.scale.findData(-2)
        else:
            self.scale.setItemText(i, label)
        self.scale.blockSignals(True)
        self.scale.setCurrentIndex(i)
        self.scale.blockSignals(False)
        self.changed.emit()

    def chosen_scale(self) -> float | None:
        """None — авто (из файла)."""
        d = self.scale.currentData()
        if d == 0:
            return None
        if d == -2:
            return self._custom
        if d == -1:
            return self._custom
        return float(d)


class _Grid:
    """Сеточный индекс точек привязки (неповёрнутые координаты)."""

    def __init__(self, flat: list[float], cell: float = 16.0):
        self.cell = cell
        self.cells: dict = {}
        for i in range(0, len(flat) - 1, 2):
            x, y = flat[i], flat[i + 1]
            self.cells.setdefault((int(x // cell), int(y // cell)), []).append((x, y))

    def nearest(self, x: float, y: float, r: float):
        best = None
        c = self.cell
        n = int(r // c) + 1
        cx, cy = int(x // c), int(y // c)
        for i in range(cx - n, cx + n + 1):
            for j in range(cy - n, cy + n + 1):
                for px, py in self.cells.get((i, j), ()):
                    d = (px - x) ** 2 + (py - y) ** 2
                    if d <= r * r and (best is None or d < best[0]):
                        best = (d, px, py)
        return (best[1], best[2]) if best else None


class MeasureTool:
    def __init__(self, tab, panel: MeasurePanel):
        self.tab = tab
        self.panel = panel
        self.page = None
        self.pts: list[tuple[float, float]] = []   # неповёрнутые координаты
        self.live: list[QGraphicsItem] = []
        self.done: list[dict] = []
        self.snaps: dict = {}
        self.vps: dict = {}
        self._pending: set = set()
        self.last_seg_pt: float | None = None
        panel.changed.connect(self._relabel_all)
        panel.calibrate.connect(self.calibrate)
        panel.toDocument.connect(self.to_document)
        panel.clearAll.connect(self.clear_all)

    # ------------------------------------------------------------ данные листа

    def _ensure_snap(self, page) -> None:
        pid = page.pid
        if pid in self.snaps or pid in self._pending:
            return
        ref = self.tab.model.page_ref(pid)
        self._pending.add(pid)

        def done(res, pid=pid):
            self._pending.discard(pid)
            self.snaps[pid] = _Grid(res[2])
            import pymupdf as fitz
            self.vps[pid] = [{"rect": fitz.Rect(v["rect"]), "scale": v["scale"]} for v in res[3]]
            self._update_file_scale(pid)

        def fail(msg, pid=pid):
            self._pending.discard(pid)
            self.snaps[pid] = _Grid([])
            self.vps[pid] = []

        self.tab.service.submit("snap", (ref["src"], ref["idx"]), sids=[ref["src"]], priority=-4,
                                callback=done, errback=fail)

    def _update_file_scale(self, pid) -> None:
        vps = [v for v in self.vps.get(pid, []) if v["scale"] != 1]
        if vps:
            self.panel.file_scale.setText("В файле указан масштаб: " + ", ".join(f"1:{v['scale']:g}" for v in vps) +
                                          " (видовой экран AutoCAD). В режиме «авто» он и используется.")
        else:
            self.panel.file_scale.setText("Масштаб в файле не указан — выберите его или откалибруйте.")

    def scale_for(self, pid, pt) -> float:
        s = self.panel.chosen_scale()
        if s:
            return s
        found = M.scale_at(self.vps.get(pid, []), pt[0], pt[1]) if pt else None
        return found or 1.0

    # ------------------------------------------------------------ события мыши

    def _point(self, page, lp: QPointF, e) -> tuple[float, float]:
        x, y = page.to_unrot(lp)
        if self.panel.snap.isChecked() and page.pid in self.snaps:
            r = 9.0 / max(0.05, self.tab.view.zoom)
            hit = self.snaps[page.pid].nearest(x, y, r)
            if hit is not None:
                self._snap_mark(page, hit)
                return hit
        self._snap_mark(page, None)
        if e is not None and e.modifiers() & Qt.ShiftModifier and self.pts:
            px, py = self.pts[-1]
            a = page.to_disp(px, py)
            dx, dy = lp.x() - a.x(), lp.y() - a.y()
            ang = round(math.degrees(math.atan2(dy, dx)) / 15) * 15
            L = math.hypot(dx, dy)
            q = QPointF(a.x() + L * math.cos(math.radians(ang)), a.y() + L * math.sin(math.radians(ang)))
            return page.to_unrot(q)
        return x, y

    _mark = None

    def _snap_mark(self, page, pt) -> None:
        if self._mark is not None and self._mark.scene():
            self._mark.scene().removeItem(self._mark)
        self._mark = None
        if pt is None:
            return
        p = page.to_disp(*pt)
        s = 5.0 / max(0.05, self.tab.view.zoom)
        it = QGraphicsRectItem(QRectF(p.x() - s, p.y() - s, 2 * s, 2 * s), page)
        it.setPen(QPen(QColor(30, 150, 60), 0))
        it.setZValue(60)
        self._mark = it

    def click(self, page, lp: QPointF, e) -> None:
        self._ensure_snap(page)
        if self.page is not None and page is not self.page:
            self.cancel()
        self.page = page
        pt = self._point(page, lp, e)
        self.pts.append(pt)
        if self.panel.kind_key() == "dist" and len(self.pts) == 2:
            self.finish()
            return
        self._draw_live(None)

    def move(self, page, lp: QPointF, e) -> None:
        if page is None:
            return
        self._ensure_snap(page)
        if self.page is not None and page is not self.page:
            return
        pt = self._point(page, lp, e)
        if self.pts:
            self._draw_live(pt)

    def finish(self) -> None:
        k = self.panel.kind_key()
        need = 2 if k in ("dist", "poly") else 3
        if self.page is None or len(self.pts) < need:
            return
        pts = list(self.pts)
        m = {"pid": self.page.pid, "pts": pts, "kind": k, "items": []}
        self.done.append(m)
        self._clear_live()
        self.pts = []
        self._draw_done(m)
        self._relabel_all()
        if k == "dist":
            self.last_seg_pt = M.length(pts)

    def cancel(self) -> None:
        self._clear_live()
        self.pts = []

    def clear_all(self) -> None:
        self.cancel()
        for m in self.done:
            for it in m["items"]:
                if it.scene():
                    it.scene().removeItem(it)
        self.done = []
        self.panel.list.clear()
        self.panel.result.setText("—")
        self.panel.sub.setText("")

    # ------------------------------------------------------------ подписи

    def label(self, pid, pts, kind) -> tuple[str, str]:
        sc = self.scale_for(pid, pts[0])
        if kind == "area":
            closed = pts + [pts[0]]
            main = M.fmt_area(M.area(pts), sc)
            sub = f"периметр {M.fmt_len(M.length(closed), sc)}"
        else:
            main = M.fmt_len(M.length(pts), sc)
            sub = ""
            if kind == "dist" and len(pts) == 2:
                (x0, y0), (x1, y1) = pts
                ang = (math.degrees(math.atan2(-(y1 - y0), x1 - x0))) % 180
                sub = f"угол {ang:.1f}°".replace(".", ",")
        paper = M.fmt_len(M.length(pts if kind != "area" else pts + [pts[0]]), 1)
        scale_txt = "на листе" if sc == 1 else f"1:{sc:g}"
        return main, f"{sub + ', ' if sub else ''}{scale_txt}; на бумаге {paper}"

    def _relabel_all(self) -> None:
        self.panel.list.clear()
        for m in self.done:
            main, sub = self.label(m["pid"], m["pts"], m["kind"])
            m["label"] = main
            n = self.tab.model.page_index(m["pid"]) + 1
            self.panel.list.addItem(f"Лист {n}: {main}  ({sub})")
            for it in m["items"]:
                if isinstance(it, QGraphicsSimpleTextItem):
                    it.setText(main)
        if self.done:
            main, sub = self.label(self.done[-1]["pid"], self.done[-1]["pts"], self.done[-1]["kind"])
            self.panel.result.setText(main)
            self.panel.sub.setText(sub)

    # ------------------------------------------------------------ рисование

    def _path_items(self, page, pts, closed, live: bool) -> list:
        path = QPainterPath(page.to_disp(*pts[0]))
        for p in pts[1:]:
            path.lineTo(page.to_disp(*p))
        if closed:
            path.closeSubpath()
        z = max(0.05, self.tab.view.zoom)
        it = QGraphicsPathItem(path, page)
        pen = QPen(COLOR, 1.6 / z)
        if live:
            pen.setStyle(Qt.DashLine)
        it.setPen(pen)
        if closed:
            it.setBrush(QColor(214, 69, 47, 35))
        it.setZValue(55)
        items = [it]
        for p in pts:
            d = page.to_disp(*p)
            r = 2.5 / z
            dot = QGraphicsEllipseItem(QRectF(d.x() - r, d.y() - r, 2 * r, 2 * r), page)
            dot.setBrush(COLOR)
            dot.setPen(Qt.NoPen)
            dot.setZValue(56)
            items.append(dot)
        return items

    def _text_item(self, page, text, at: QPointF) -> QGraphicsSimpleTextItem:
        z = max(0.05, self.tab.view.zoom)
        t = QGraphicsSimpleTextItem(text, page)
        f = QFont()
        f.setPointSizeF(12)
        f.setBold(True)
        t.setFont(f)
        t.setBrush(COLOR)
        t.setScale(1.0 / z)
        t.setPos(at + QPointF(6 / z, -20 / z))
        t.setZValue(57)
        bg = QGraphicsRectItem(t.boundingRect().adjusted(-3, -1, 3, 1), t)
        bg.setBrush(QColor(255, 255, 255, 220))
        bg.setPen(Qt.NoPen)
        bg.setFlag(QGraphicsItem.ItemStacksBehindParent, True)
        return t

    def _clear_live(self) -> None:
        for it in self.live:
            if it.scene():
                it.scene().removeItem(it)
        self.live = []

    def _draw_live(self, cursor) -> None:
        self._clear_live()
        page = self.page
        if page is None or not self.pts:
            return
        pts = self.pts + ([cursor] if cursor else [])
        k = self.panel.kind_key()
        if len(pts) >= 2:
            self.live = self._path_items(page, pts, k == "area" and len(pts) >= 3, True)
            main, sub = self.label(page.pid, pts, k)
            self.live.append(self._text_item(page, main, page.to_disp(*pts[-1])))
            self.panel.result.setText(main)
            self.panel.sub.setText(sub)

    def _draw_done(self, m) -> None:
        page = self.tab.view.items_by_pid.get(m["pid"])
        if page is None:
            return
        items = self._path_items(page, m["pts"], m["kind"] == "area", False)
        main, _ = self.label(m["pid"], m["pts"], m["kind"])
        pts = m["pts"]
        mid = page.to_disp((pts[0][0] + pts[1][0]) / 2, (pts[0][1] + pts[1][1]) / 2)
        items.append(self._text_item(page, main, mid))
        m["items"] = items

    def rebuild(self) -> None:
        """После перестроения листов (масштаб, повороты) — перерисовать."""
        self._clear_live()
        if self.page is not None and self.page.scene() is None:
            self.page = None
            self.pts = []
        for m in list(self.done):
            if self.tab.model.page_ref(m["pid"]) is None:
                self.done.remove(m)
                continue
            for it in m["items"]:
                if it.scene():
                    it.scene().removeItem(it)
            self._draw_done(m)

    # ------------------------------------------------------------ калибровка и запись в PDF

    def calibrate(self) -> None:
        last = next((m for m in reversed(self.done) if m["kind"] == "dist"), None)
        if last is None:
            self.tab.toast("Сначала измерьте отрезок известной длины (режим «Расстояние»)")
            return
        pt_len = M.length(last["pts"])
        real, ok = QInputDialog.getDouble(self.tab, "Калибровка", "Длина этого отрезка в натуре, м:", 10.0,
                                          0.001, 1e7, 3)
        if not ok or real <= 0:
            return
        n = real * 1000.0 / M.paper_mm(pt_len)
        self.panel.set_custom(round(n, 2))
        self.tab.toast(f"Масштаб 1:{n:.2f}".replace(".", ","))

    def to_document(self) -> None:
        if not self.done:
            self.tab.toast("Нет измерений")
            return
        pages = set()
        for m in self.done:
            self.tab.model.add_obj(m["pid"], {"t": "measure", "pts": [list(p) for p in m["pts"]],
                                              "closed": m["kind"] == "area", "label": m.get("label", ""),
                                              "color": COLOR.name()}, commit=False)
            pages.add(m["pid"])
        self.tab.model.commit()
        self.clear_all()
        self.tab.after_change(pages=list(pages))
        self.tab.toast("Измерения записаны в документ (отменить — ⌘Z)")
