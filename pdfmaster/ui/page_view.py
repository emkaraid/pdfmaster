"""Область просмотра: листы друг под другом, масштаб, правка на месте.

Координаты: сцена в пунктах при масштабе 100 %; у каждого листа свои
локальные координаты «как он виден» (0,0 — верхний левый угол). Объекты
модели хранятся в неповёрнутых координатах листа MuPDF; перевод — через
DocModel.display_matrix().
"""
from __future__ import annotations

import hashlib
import json
import math

import pymupdf as fitz
from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (QColor, QFont, QFontMetricsF, QImage, QKeySequence, QPainter,
                           QPainterPath, QPen, QPixmap, QPolygonF, QTextCursor, QTextOption, QTransform)
from PySide6.QtWidgets import (QApplication, QGraphicsItem, QGraphicsObject, QGraphicsPolygonItem,
                               QGraphicsRectItem, QGraphicsScene, QGraphicsTextItem, QGraphicsView, QMenu)

from ..core import bake as B
from ..core import fonts as F
from ..core.textlines import join_para_text, line_key, paragraph_of
from . import qtfonts

GAP = 18.0          # промежуток между листами, пт
MARGIN = 24.0
MAX_BASE_PIX = 14e6  # больше — дорисовываем видимую часть отдельной плиткой
SEL = QColor(47, 111, 214)
HOVER = QColor(47, 111, 214, 46)


def _qimage(res) -> QImage:
    _, _, w, h, x, y, data = res
    img = QImage(data, w, h, 3 * w, QImage.Format_RGB888)
    return img.copy()


def _objs_key(objs, skip) -> str:
    return hashlib.sha1(json.dumps([objs, sorted(skip)], sort_keys=True, ensure_ascii=False).encode()).hexdigest()


# =============================================================== лист


class PageItem(QGraphicsObject):
    def __init__(self, view: "PageView", pid: str):
        super().__init__()
        self.view = view
        self.pid = pid
        self.setFlag(QGraphicsItem.ItemUsesExtendedStyleOption, True)
        self.setAcceptHoverEvents(True)
        self.base: QPixmap | None = None
        self.base_scale = 0.0
        self.base_key = None
        self.tile: QPixmap | None = None
        self.tile_rect = QRectF()
        self.tile_scale = 0.0
        self.tile_key = None
        self.ghosts: list[QGraphicsItem] = []
        self.ghost_key = None
        self.hover_poly: QGraphicsPolygonItem | None = None
        self.search_items: list[QGraphicsItem] = []
        self.sync_geometry()

    # ------------------------------------------------------------ геометрия

    @property
    def model(self):
        return self.view.model

    @property
    def ref(self) -> dict:
        return self.model.page_ref(self.pid)

    def sync_geometry(self) -> None:
        ref = self.ref
        self.prepareGeometryChange()
        self.w, self.h = self.model.display_size(ref)
        self.D = self.model.display_matrix(ref)
        self.Dinv = ~self.D
        self.total_rot = self.model.total_rotation(ref)

    def boundingRect(self) -> QRectF:
        return QRectF(-4, -4, self.w + 8, self.h + 8)

    def to_disp(self, x: float, y: float) -> QPointF:
        p = fitz.Point(x, y) * self.D
        return QPointF(p.x, p.y)

    def to_unrot(self, pt: QPointF) -> tuple[float, float]:
        p = fitz.Point(pt.x(), pt.y()) * self.Dinv
        return p.x, p.y

    def disp_angle(self, unrot_angle: float) -> float:
        return (unrot_angle + self.total_rot) % 360

    def dir_angle(self, d) -> float:
        """Угол направления строки на экране, градусы."""
        return (math.degrees(math.atan2(d[1], d[0])) + self.total_rot) % 360

    # ------------------------------------------------------------ содержимое

    def bake_objs(self) -> tuple[list, list]:
        """Что запекать в предпросмотр: всё, кроме картинок (их рисует Qt)."""
        objs = [o for o in self.model.page_objs(self.pid) if o.get("t") != "image"]
        ed = self.view.editing
        skip: list = []
        if ed and ed.pid == self.pid:
            oid = ed.obj["id"]
            objs = [o for o in objs if o.get("id") != oid]
            if ed.kind in ("line", "para"):
                # пока идёт набор — старый текст убран целиком, новый показывает редактор
                objs.append(dict(ed.obj, text="", style=None))
                skip = [oid]
        return objs, skip

    def content_key(self):
        ref = self.ref
        objs, skip = self.bake_objs()
        return (ref["src"], ref["idx"], ref["rot"], _objs_key(objs, skip) if objs else "")

    def paint(self, p: QPainter, opt, widget=None) -> None:
        r = QRectF(0, 0, self.w, self.h)
        # тень
        p.setPen(Qt.NoPen)
        for i, a in ((3, 18), (2, 26), (1, 40)):
            p.setBrush(QColor(0, 0, 0, a))
            p.drawRect(r.adjusted(-i * 0.5, -i * 0.3, i * 0.5, i * 0.9))
        p.setBrush(Qt.white)
        p.drawRect(r)
        if self.base is not None:
            p.setRenderHint(QPainter.SmoothPixmapTransform, True)
            p.drawPixmap(r, self.base, QRectF(self.base.rect()))
        if self.tile is not None and self.tile_key == self.base_key:
            p.drawPixmap(self.tile_rect, self.tile, QRectF(self.tile.rect()))

    def set_base(self, img: QImage, scale: float, key) -> None:
        self.base = QPixmap.fromImage(img)
        self.base_scale = scale
        if key != self.base_key:
            self.tile = None
        self.base_key = key
        if key == self.content_key():
            if self.ghosts:
                self.clear_ghosts()
            ed = self.view.editing
            if ed is not None and ed.page is self:
                for m in ed.masks:  # старая строка уже убрана из рендера — заплатка не нужна
                    m.setVisible(False)
        self.update()

    def set_tile(self, img: QImage, rect: QRectF, scale: float, key) -> None:
        self.tile = QPixmap.fromImage(img)
        self.tile_rect = rect
        self.tile_scale = scale
        self.tile_key = key
        self.update()

    def clear_ghosts(self) -> None:
        for g in self.ghosts:
            if g.scene():
                g.scene().removeItem(g)
        self.ghosts = []

    def background_at(self, poly: QPolygonF) -> QColor:
        """Цвет фона под строкой (по краю её прямоугольника на текущем рендере)."""
        if self.base is None:
            return QColor(Qt.white)
        img = self.base.toImage()
        k = img.width() / max(1.0, self.w)
        br = poly.boundingRect().adjusted(-1.5, -1.5, 1.5, 1.5)
        pts = []
        for t in range(12):
            for (x, y) in ((br.left() + br.width() * t / 11, br.top()), (br.left() + br.width() * t / 11, br.bottom()),
                           (br.left(), br.top() + br.height() * t / 11), (br.right(), br.top() + br.height() * t / 11)):
                xi, yi = int(x * k), int(y * k)
                if 0 <= xi < img.width() and 0 <= yi < img.height():
                    pts.append(QColor(img.pixel(xi, yi)))
        if not pts:
            return QColor(Qt.white)
        pts.sort(key=lambda c: c.lightness())
        return pts[len(pts) * 3 // 4]

    # ------------------------------------------------------------ наведение (правка текста)

    def hoverMoveEvent(self, e) -> None:
        if self.view.mode == "select" and not self.view.editing:
            lines = self.view.tab.view_lines(self.pid)
            over = bool(lines) and self.view._sel_hit(self, e.pos(), lines) is not None
            self.setCursor(Qt.IBeamCursor if over else Qt.OpenHandCursor)
            return
        if self.view.mode != "edit" or self.view.editing:
            self._set_hover(None)
            return
        ln = self.view.line_at(self, e.pos())
        self._set_hover(ln)

    def hoverLeaveEvent(self, e) -> None:
        self._set_hover(None)

    def _set_hover(self, ln) -> None:
        if ln is None:
            if self.hover_poly is not None:
                self.hover_poly.setVisible(False)
            self.setCursor(Qt.ArrowCursor if self.view.mode == "select" else self.view.mode_cursor())
            return
        poly = self.view.line_poly(self, ln)
        if self.hover_poly is None:
            self.hover_poly = QGraphicsPolygonItem(self)
            self.hover_poly.setBrush(HOVER)
            self.hover_poly.setPen(QPen(QColor(47, 111, 214, 150), 0))
            self.hover_poly.setZValue(5)
        self.hover_poly.setPolygon(poly)
        self.hover_poly.setVisible(True)
        self.setCursor(Qt.IBeamCursor)


# =============================================================== объекты поверх листа


class ObjItem(QGraphicsObject):
    """Картинка (печать/подпись) или новый текст: выделение, перемещение, размер, поворот."""

    HANDLE = 5.0

    def __init__(self, page: PageItem, oid: str):
        super().__init__(page)
        self.page = page
        self.oid = oid
        self.selected = False
        self.setZValue(20)
        self.setAcceptHoverEvents(True)
        self._drag = None
        self.sync()

    @property
    def view(self) -> "PageView":
        return self.page.view

    @property
    def obj(self) -> dict:
        return self.view.model.find_obj(self.oid)[1]

    def sync(self) -> None:
        o = self.obj
        if o is None:
            return
        self.prepareGeometryChange()
        if o["t"] == "image":
            c = self.page.to_disp(o["cx"], o["cy"])
            self.setPos(c)
            self.setRotation(self.page.disp_angle(o.get("angle", 0)))
            self.bw, self.bh = float(o["w"]), float(o["h"])
            self.box = QRectF(-self.bw / 2, -self.bh / 2, self.bw, self.bh)
        else:  # text
            _, w, h = B.text_obj_layout(o)
            self.setPos(self.page.to_disp(o["x"], o["y"]))
            self.setRotation(self.page.disp_angle(o.get("angle", 0)))
            self.box = QRectF(0, 0, max(w, 4), max(h, 4))
        self.update()

    def handle_scale(self) -> float:
        return 1.0 / max(0.05, self.view.zoom)

    def boundingRect(self) -> QRectF:
        s = self.handle_scale() * (self.HANDLE + 22)
        return self.box.adjusted(-s, -s, s, s)

    def shape(self) -> QPainterPath:
        path = QPainterPath()
        path.addRect(self.box)
        if self.selected:
            for h in self._handles().values():
                path.addEllipse(h, self.HANDLE * 1.6 * self.handle_scale(), self.HANDLE * 1.6 * self.handle_scale())
        return path

    def _handles(self) -> dict:
        b = self.box
        hs = {"tl": b.topLeft(), "tr": b.topRight(), "bl": b.bottomLeft(), "br": b.bottomRight()}
        if self.obj and self.obj["t"] == "image":
            hs["rot"] = QPointF(b.center().x(), b.top() - 18 * self.handle_scale())
        return hs

    def paint(self, p: QPainter, opt, widget=None) -> None:
        o = self.obj
        if o is None:
            return
        if o["t"] == "image":
            img = self.view.tab.asset_image(o["asset"])
            if img is not None:
                p.setRenderHint(QPainter.SmoothPixmapTransform, True)
                p.drawImage(self.box, img)
        if not self.selected:
            if o["t"] == "text" and self.view.mode in ("select", "addtext"):
                pen = QPen(QColor(47, 111, 214, 70), 0, Qt.DotLine)
                p.setPen(pen)
                p.setBrush(Qt.NoBrush)
                p.drawRect(self.box)
            return
        s = self.handle_scale()
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setPen(QPen(SEL, 1.4 * s))
        p.setBrush(Qt.NoBrush)
        p.drawRect(self.box)
        hs = self._handles()
        if "rot" in hs:
            p.drawLine(QPointF(self.box.center().x(), self.box.top()), hs["rot"])
        p.setBrush(Qt.white)
        for k, pt in hs.items():
            if o["t"] == "text" and k != "br":
                continue
            p.drawEllipse(pt, self.HANDLE * s, self.HANDLE * s)

    def _hit_handle(self, pos: QPointF):
        if not self.selected:
            return None
        r = self.HANDLE * 2.0 * self.handle_scale()
        for k, pt in self._handles().items():
            if self.obj["t"] == "text" and k != "br":
                continue
            if (pt - pos).manhattanLength() < r * 1.4:
                return k
        return None

    def hoverMoveEvent(self, e) -> None:
        h = self._hit_handle(e.pos())
        if h == "rot":
            self.setCursor(Qt.CrossCursor)
        elif h in ("tl", "br"):
            self.setCursor(Qt.SizeFDiagCursor)
        elif h in ("tr", "bl"):
            self.setCursor(Qt.SizeBDiagCursor)
        else:
            self.setCursor(Qt.SizeAllCursor if self.view.mode != "edit" else Qt.IBeamCursor)

    def mousePressEvent(self, e) -> None:
        if e.button() != Qt.LeftButton:
            e.ignore()
            return
        o = self.obj
        if o["t"] == "text" and self.view.mode in ("edit", "addtext"):
            self.view.start_text_obj_edit(self.page, self.oid)
            e.accept()
            return
        self.view.select(self.oid)
        h = self._hit_handle(e.pos())
        self._drag = {"handle": h, "start": e.scenePos(), "o0": dict(o), "moved": False,
                      "pos0": self.pos(), "rot0": self.rotation()}
        e.accept()

    def mouseDoubleClickEvent(self, e) -> None:
        if self.obj and self.obj["t"] == "text":
            self.view.start_text_obj_edit(self.page, self.oid)
        e.accept()

    def mouseMoveEvent(self, e) -> None:
        d = self._drag
        if not d:
            return
        delta = e.scenePos() - d["start"]
        if not d["moved"] and delta.manhattanLength() * self.view.zoom < 3:
            return
        d["moved"] = True
        o = self.obj
        o0 = d["o0"]
        page = self.page
        if d["handle"] is None:  # перемещение
            lp = page.mapFromScene(e.scenePos())
            sp = page.mapFromScene(d["start"])
            if o["t"] == "image":
                c0 = page.to_disp(o0["cx"], o0["cy"])
                o["cx"], o["cy"] = page.to_unrot(c0 + (lp - sp))
            else:
                c0 = page.to_disp(o0["x"], o0["y"])
                o["x"], o["y"] = page.to_unrot(c0 + (lp - sp))
        elif d["handle"] == "rot":
            c = self.mapToScene(QPointF(0, 0))
            v = e.scenePos() - c
            ang = math.degrees(math.atan2(v.y(), v.x())) + 90.0
            snap = 15.0 if e.modifiers() & Qt.ShiftModifier else 0.0
            disp = ang % 360
            if snap:
                disp = round(disp / snap) * snap
            else:
                for t in (0, 90, 180, 270, 360):
                    if abs(disp - t) < 4:
                        disp = t % 360
            o["angle"] = (disp - page.total_rot) % 360
        else:  # размер с сохранением пропорций
            if o["t"] == "image":
                opp = {"tl": "br", "tr": "bl", "bl": "tr", "br": "tl"}[d["handle"]]
                w0, h0 = float(o0["w"]), float(o0["h"])
                box0 = QRectF(-w0 / 2, -h0 / 2, w0, h0)
                anchor = {"tl": box0.topLeft(), "tr": box0.topRight(), "bl": box0.bottomLeft(),
                          "br": box0.bottomRight()}[opp]
                # текущая позиция мыши в системе исходного объекта
                t = QTransform()
                t.rotate(d["rot0"])
                tinv, _ = t.inverted()
                mp = tinv.map(e.scenePos() - self.parentItem().mapToScene(d["pos0"]))
                vx, vy = abs(mp.x() - anchor.x()), abs(mp.y() - anchor.y())
                k = max(vx / w0, vy / h0, 8.0 / w0)
                nw, nh = w0 * k, h0 * k
                sx = 1 if mp.x() >= anchor.x() else -1
                sy = 1 if mp.y() >= anchor.y() else -1
                cl = QPointF(anchor.x() + sx * nw / 2, anchor.y() + sy * nh / 2)
                cs = self.parentItem().mapFromScene(self.parentItem().mapToScene(d["pos0"]) + t.map(cl))
                o["w"], o["h"] = nw, nh
                o["cx"], o["cy"] = page.to_unrot(cs)
            else:
                # размер шрифта нового текста — тянем правый нижний угол
                lp = self.mapFromScene(e.scenePos())
                d.setdefault("h0", max(4.0, self.box.height()))
                k = max(0.2, lp.y() / d["h0"])
                o["size"] = max(3.0, round(float(o0["size"]) * k * 2) / 2)
        self.sync()
        self.view.tab.update_props()

    def mouseReleaseEvent(self, e) -> None:
        d = self._drag
        self._drag = None
        if d and d["moved"]:
            self.view.model.commit()
            self.view.tab.after_change(pages=[self.page.pid])


# =============================================================== редактор на месте


class InlineEditor(QGraphicsTextItem):
    def __init__(self, parent: PageItem, multiline: bool, on_finish):
        super().__init__(parent)
        self.multiline = multiline
        self.on_finish = on_finish
        self.setTextInteractionFlags(Qt.TextEditorInteraction)
        self.document().setDocumentMargin(0)
        opt = QTextOption()
        opt.setWrapMode(QTextOption.WordWrap if multiline else QTextOption.NoWrap)
        self.document().setDefaultTextOption(opt)
        self.setZValue(40)
        self._done = False

    def keyPressEvent(self, e) -> None:
        if e.key() == Qt.Key_Escape:
            self.finish(True)
            return
        if e.matches(QKeySequence.Paste):
            # только простой текст, без чужих шрифтов; в строке — без переводов строк
            txt = QApplication.clipboard().text()
            txt = txt.replace("\r\n", "\n").replace("\r", "\n")
            if not self.multiline:
                txt = " ".join(txt.split("\n"))
            self.textCursor().insertText(txt)
            return
        if e.key() in (Qt.Key_Return, Qt.Key_Enter):
            if not self.multiline or e.modifiers() & (Qt.ControlModifier | Qt.MetaModifier):
                self.finish(True)
                return
        super().keyPressEvent(e)

    def focusOutEvent(self, e) -> None:
        super().focusOutEvent(e)
        if e.reason() in (Qt.MouseFocusReason, Qt.TabFocusReason, Qt.BacktabFocusReason, Qt.ActiveWindowFocusReason):
            # клик в панель свойств не должен завершать правку
            fw = QApplication.focusWidget()
            if e.reason() == Qt.ActiveWindowFocusReason or (fw is not None and fw is not self.scene().views()[0]
                                                            and self._is_props(fw)):
                return
            QTimer.singleShot(0, lambda: self.finish(True))

    @staticmethod
    def _is_props(w) -> bool:
        while w is not None:
            if w.objectName() in ("propsPanel", "sidePanel"):
                return True
            w = w.parentWidget()
        return False

    def finish(self, commit: bool) -> None:
        if self._done:
            return
        self._done = True
        self.setTextInteractionFlags(Qt.NoTextInteraction)
        self.on_finish(commit)

    def paint(self, p, opt, w=None):
        # без серой рамки фокуса Qt
        from PySide6.QtWidgets import QStyle
        opt.state &= ~QStyle.State_HasFocus
        opt.state &= ~QStyle.State_Selected
        super().paint(p, opt, w)
        if not self._done:
            p.setPen(QPen(SEL, 0))
            p.setBrush(Qt.NoBrush)
            p.drawRect(self.boundingRect().adjusted(-1.5, -1, 1.5, 1))


class EditSession:
    """Текущая правка: строка/абзац документа или новый текст."""

    def __init__(self, page: PageItem, obj: dict, is_new: bool, kind: str):
        self.page = page
        self.pid = page.pid
        self.obj = obj
        self.is_new = is_new
        self.kind = kind  # "line" | "para" | "text"
        self.editor: InlineEditor | None = None
        self.masks: list[QGraphicsItem] = []
        self.orig_text = obj.get("text", "")


# =============================================================== вид


class PageView(QGraphicsView):
    zoomChanged = Signal(float)
    currentPageChanged = Signal(int)
    selectionChanged = Signal()
    editingChanged = Signal()

    def __init__(self, tab):
        super().__init__()
        self.tab = tab
        self.setScene(QGraphicsScene(self))
        self.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform | QPainter.TextAntialiasing)
        self.setViewportUpdateMode(QGraphicsView.SmartViewportUpdate)
        self.setTransformationAnchor(QGraphicsView.NoAnchor)
        self.setResizeAnchor(QGraphicsView.NoAnchor)
        self.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        self.setFrameShape(QGraphicsView.NoFrame)
        from . import theme
        self.setBackgroundBrush(theme.color("canvas"))
        self.setMouseTracking(True)
        self.viewport().setAttribute(Qt.WA_AcceptTouchEvents, False)
        self.grabGesture(Qt.PinchGesture)
        self.zoom = 1.0
        self.fit = "width"
        self.mode = "select"
        self.items_by_pid: dict[str, PageItem] = {}
        self.order: list[str] = []
        self.obj_items: dict[str, ObjItem] = {}
        self.selected: str | None = None
        self.editing: EditSession | None = None
        self.rubber: QGraphicsRectItem | None = None
        self.rubber_start = None
        self.erase_items: list[QGraphicsItem] = []
        self._render_timer = QTimer(self, singleShot=True, interval=40, timeout=self.update_renders)
        self._cur_page = 0
        self.verticalScrollBar().valueChanged.connect(self._on_scroll)
        self.horizontalScrollBar().valueChanged.connect(lambda *_: self.schedule_render())

    @property
    def model(self):
        return self.tab.model

    # ------------------------------------------------------------ построение

    def rebuild(self) -> None:
        """Пересоздаёт листы по модели, сохраняя готовые картинки."""
        self.finish_edit(True)
        old = self.items_by_pid
        sc = self.scene()
        for it in list(self.obj_items.values()):
            if it.scene():
                sc.removeItem(it)
        self.obj_items.clear()
        new: dict[str, PageItem] = {}
        for ref in self.model.pages:
            it = old.pop(ref["id"], None)
            if it is None:
                it = PageItem(self, ref["id"])
                sc.addItem(it)
            else:
                it.sync_geometry()
                if it.base_key and it.base_key[:3] != (ref["src"], ref["idx"], ref["rot"]):
                    it.base = None
                    it.tile = None
                    it.base_key = None
            new[ref["id"]] = it
        for it in old.values():
            sc.removeItem(it)
        self.items_by_pid = new
        self.order = [r["id"] for r in self.model.pages]
        self._layout()
        for pid in self.order:
            self._sync_objs(pid)
        if self.selected and self.selected not in self.obj_items:
            self.selected = None
            self.selectionChanged.emit()
        self._update_erase_items()
        self.apply_fit()
        self.schedule_render()

    def _layout(self) -> None:
        maxw = max([self.items_by_pid[p].w for p in self.order], default=600)
        y = MARGIN
        for pid in self.order:
            it = self.items_by_pid[pid]
            it.setPos((maxw - it.w) / 2 + MARGIN, y)
            y += it.h + GAP
        self.scene().setSceneRect(QRectF(0, 0, maxw + 2 * MARGIN, y - GAP + MARGIN))

    def _sync_objs(self, pid: str) -> None:
        page = self.items_by_pid.get(pid)
        if page is None:
            return
        want = {o["id"] for o in self.model.page_objs(pid) if o["t"] in ("image", "text")}
        for oid in [k for k, v in self.obj_items.items() if v.page is page and k not in want]:
            it = self.obj_items.pop(oid)
            if it.scene():
                self.scene().removeItem(it)
        for o in self.model.page_objs(pid):
            if o["t"] not in ("image", "text"):
                continue
            it = self.obj_items.get(o["id"])
            if it is None:
                it = ObjItem(page, o["id"])
                self.obj_items[o["id"]] = it
            else:
                it.sync()
            it.selected = o["id"] == self.selected
            it.setVisible(not (self.editing and self.editing.obj.get("id") == o["id"]))

    def refresh_pages(self, pids=None) -> None:
        """Содержимое листов изменилось (объекты)."""
        self._clear_tsel()
        for pid in (pids or self.order):
            if pid in self.items_by_pid:
                self._sync_objs(pid)
        if self.selected and self.selected not in self.obj_items and self.model.find_obj(self.selected)[1] is None:
            self.selected = None
            self.selectionChanged.emit()
        self._update_erase_items()
        self.schedule_render()

    # ------------------------------------------------------------ масштаб

    def page_item_at_index(self, i: int) -> PageItem | None:
        if 0 <= i < len(self.order):
            return self.items_by_pid[self.order[i]]
        return None

    def set_zoom(self, z: float, anchor: QPointF | None = None, keep_fit: bool = False) -> None:
        z = max(0.05, min(32.0, z))
        if anchor is None:
            anchor = QPointF(self.viewport().width() / 2, self.viewport().height() / 2)
        scene_pt = self.mapToScene(anchor.toPoint())
        self.zoom = z
        self.setTransform(QTransform.fromScale(z, z))
        new_pt = self.mapFromScene(scene_pt)
        d = new_pt - anchor.toPoint()
        self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() + d.x())
        self.verticalScrollBar().setValue(self.verticalScrollBar().value() + d.y())
        if not keep_fit:
            self.fit = None
        for it in self.obj_items.values():
            it.prepareGeometryChange()
        self.zoomChanged.emit(z)
        self.schedule_render()

    def zoom_by(self, k: float, anchor=None) -> None:
        self.set_zoom(self.zoom * k, anchor)

    def apply_fit(self) -> None:
        if not self.order or self.fit is None:
            return
        vw = max(100, self.viewport().width() - 2 * 14)
        vh = max(100, self.viewport().height() - 2 * 10)
        if self.fit == "width":
            maxw = max(self.items_by_pid[p].w for p in self.order)
            z = vw / (maxw + 2 * MARGIN)
        else:
            it = self.page_item_at_index(self._cur_page) or self.items_by_pid[self.order[0]]
            z = min(vw / (it.w + 2 * MARGIN), vh / (it.h + GAP))
        cur = self._cur_page
        self.set_zoom(z, keep_fit=True)
        self.go_to_page(cur, smooth=False)

    def set_fit(self, fit: str | None) -> None:
        self.fit = fit
        self.apply_fit()

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        if self.fit:
            QTimer.singleShot(0, self.apply_fit)
        self.schedule_render()

    def wheelEvent(self, e) -> None:
        if e.modifiers() & (Qt.ControlModifier | Qt.MetaModifier):
            dy = e.angleDelta().y() or e.pixelDelta().y()
            if dy:
                self.zoom_by(1.0 + max(-0.5, min(0.5, dy / 600.0)), e.position())
            e.accept()
            return
        super().wheelEvent(e)

    def viewportEvent(self, e) -> bool:
        if e.type() == QEvent.NativeGesture:
            if e.gestureType() == Qt.ZoomNativeGesture:
                self.zoom_by(1.0 + e.value(), e.position())
                return True
            if e.gestureType() == Qt.SmartZoomNativeGesture:
                self.set_fit("page" if self.fit == "width" else "width")
                return True
        return super().viewportEvent(e)

    def event(self, e) -> bool:
        if e.type() == QEvent.ShortcutOverride and self.editing is not None:
            # во время набора стандартные клавиши правки текста — редактору, а не меню
            if e.matches(QKeySequence.Undo) or e.matches(QKeySequence.Redo):
                return super().event(e)
            mods = e.modifiers() & ~Qt.KeypadModifier
            if e.key() in (Qt.Key_Backspace, Qt.Key_Delete, Qt.Key_Left, Qt.Key_Right, Qt.Key_Up, Qt.Key_Down,
                           Qt.Key_Home, Qt.Key_End) or (mods in (Qt.NoModifier, Qt.ShiftModifier) and e.text()) or \
                    any(e.matches(k) for k in (QKeySequence.SelectAll, QKeySequence.Copy, QKeySequence.Paste,
                                               QKeySequence.Cut, QKeySequence.DeleteStartOfWord,
                                               QKeySequence.DeleteEndOfLine)):
                e.accept()
                return True
        if e.type() == QEvent.Gesture:
            g = e.gesture(Qt.PinchGesture)
            if g is not None:
                self.zoom_by(g.scaleFactor(), self.mapFromGlobal(g.centerPoint().toPoint()).toPointF())
                return True
        return super().event(e)

    # ------------------------------------------------------------ навигация

    def _on_scroll(self, *_):
        self._track_current()
        self.schedule_render()

    def _track_current(self) -> None:
        if not self.order:
            return
        vis = self.mapToScene(self.viewport().rect()).boundingRect()
        mid = vis.top() + vis.height() * 0.35
        cur = 0
        for i, pid in enumerate(self.order):
            it = self.items_by_pid[pid]
            if it.y() <= mid:
                cur = i
            else:
                break
        if cur != self._cur_page:
            self._cur_page = cur
            self.currentPageChanged.emit(cur)

    def current_page(self) -> int:
        return self._cur_page

    def go_to_page(self, i: int, smooth: bool = True) -> None:
        it = self.page_item_at_index(i)
        if it is None:
            return
        self._cur_page = i
        y = it.y() * self.zoom - 10
        self.verticalScrollBar().setValue(int(y))
        self.currentPageChanged.emit(i)

    def ensure_visible_rect(self, page: PageItem, r: QRectF) -> None:
        self.ensureVisible(page.mapRectToScene(r), 60, 80)

    # ------------------------------------------------------------ рендер

    def schedule_render(self) -> None:
        self._render_timer.start()

    def visible_pages(self, margin_px: float = 600) -> list[tuple[PageItem, QRectF]]:
        vr = self.mapToScene(self.viewport().rect().adjusted(0, -int(margin_px), 0, int(margin_px))).boundingRect()
        out = []
        for pid in self.order:
            it = self.items_by_pid[pid]
            r = QRectF(it.pos(), QRectF(0, 0, it.w, it.h).size())
            if r.intersects(vr):
                out.append((it, vr.intersected(r).translated(-it.pos())))
        return out

    def update_renders(self) -> None:
        if not self.order:
            return
        dpr = self.devicePixelRatioF()
        need = self.zoom * dpr
        tabid = id(self.tab)
        vc = self.mapToScene(self.viewport().rect()).boundingRect()
        vis = self.visible_pages()
        self._evict(vc, {it.pid for it, _ in vis})
        for it, vrect in vis:
            key = it.content_key()
            ref = it.ref
            objs, skip = it.bake_objs()
            cap = math.sqrt(MAX_BASE_PIX / max(1.0, it.w * it.h))
            want = min(need, cap)
            dist = abs((it.y() + it.h / 2) - vc.center().y()) / max(1.0, vc.height())
            prio = dist
            if it.base_key != key or it.base_scale < want * 0.97 or it.base_scale > want * 2.2:
                if it.base is None and want > 0.7:
                    # быстрый черновик, затем чёткий
                    self._submit(it, key, ref, objs, skip, min(0.6, want), None, prio - 10, ("low", tabid, it.pid))
                self._submit(it, key, ref, objs, skip, want, None, prio, ("base", tabid, it.pid))
            if need > cap * 1.03:
                tr = vrect.adjusted(-vrect.width() * 0.25, -vrect.height() * 0.25,
                                    vrect.width() * 0.25, vrect.height() * 0.25).intersected(QRectF(0, 0, it.w, it.h))
                ok = (it.tile is not None and it.tile_key == key and abs(it.tile_scale - need) < 1e-3
                      and it.tile_rect.contains(vrect.intersected(QRectF(0, 0, it.w, it.h))))
                if not ok and not tr.isEmpty():
                    clip = [tr.left() * need, tr.top() * need, tr.right() * need, tr.bottom() * need]
                    self._submit(it, key, ref, objs, skip, need, clip, prio + 0.5, ("tile", tabid, it.pid))
            elif it.tile is not None:
                it.tile = None
                it.update()

    def _evict(self, vc: QRectF, keep: set) -> None:
        """Далёкие от экрана листы держим в памяти уменьшенными (иначе сотни листов съедят гигабайты)."""
        far = vc.height() * 4
        for pid in self.order:
            if pid in keep:
                continue
            it = self.items_by_pid[pid]
            if it.base is None or it.base_scale <= 0.35:
                continue
            if abs((it.y() + it.h / 2) - vc.center().y()) > far:
                k = 0.3 / it.base_scale
                it.base = it.base.scaled(max(1, int(it.base.width() * k)), max(1, int(it.base.height() * k)),
                                         Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
                it.base_scale = 0.3
                it.tile = None

    def _submit(self, it: PageItem, key, ref, objs, skip, scale, clip, prio, jobkey) -> None:
        aids = []
        for o in objs:
            if o.get("t") == "image":
                aids.append(o["asset"])
        tab = self.tab

        def done(res, it=it, key=key, scale=scale, clip=clip, kind=jobkey[0]):
            if it.scene() is None:
                return
            img = _qimage(res)
            if clip is None:
                if kind == "low" and it.base is not None and it.base_key == key:
                    return
                it.set_base(img, scale, key)
            else:
                x, y = res[4], res[5]
                it.set_tile(img, QRectF(x / scale, y / scale, img.width() / scale, img.height() / scale), scale, key)
            tab.page_rendered(it.pid)

        tab.service.submit("render", (ref["src"], ref["idx"], ref["rot"], objs, skip, scale, clip),
                           sids=[ref["src"]], aids=aids, priority=prio, key=jobkey, callback=done)

    # ------------------------------------------------------------ режимы

    def set_mode(self, mode: str) -> None:
        self.finish_edit(True)
        self._clear_tsel()
        self.mode = mode
        for it in self.items_by_pid.values():
            if it.hover_poly is not None:
                it.hover_poly.setVisible(False)
        self.viewport().setCursor(self.mode_cursor())
        if mode != "select" and self.selected and mode not in ("stamp",):
            self.select(None)
        self._update_erase_items()
        for it in self.obj_items.values():
            it.update()
        if mode == "edit":
            self.tab.prefetch_lines([it.pid for it, _ in self.visible_pages()])

    def mode_cursor(self):
        return {"select": Qt.OpenHandCursor, "measure": Qt.CrossCursor, "edit": Qt.IBeamCursor, "addtext": Qt.IBeamCursor,
                "erase": Qt.CrossCursor, "stamp": Qt.CrossCursor}.get(self.mode, Qt.ArrowCursor)

    # ------------------------------------------------------------ выделение

    def select(self, oid: str | None) -> None:
        if self.selected == oid:
            return
        old = self.obj_items.get(self.selected) if self.selected else None
        self.selected = oid
        if old is not None:
            old.selected = False
            old.prepareGeometryChange()
            old.update()
        it = self.obj_items.get(oid) if oid else None
        if it is not None:
            it.selected = True
            it.prepareGeometryChange()
            it.update()
        for e in self.erase_items:
            e.setPen(self._erase_pen(e.data(0) == oid))
        self.selectionChanged.emit()

    def selected_obj(self):
        if not self.selected:
            return None, None
        return self.model.find_obj(self.selected)

    def delete_selected(self) -> bool:
        if not self.selected:
            return False
        pid, o = self.model.find_obj(self.selected)
        if o is None:
            return False
        self.model.remove_obj(self.selected)
        self.selected = None
        self.selectionChanged.emit()
        self.tab.after_change(pages=[pid])
        return True

    # ------------------------------------------------------------ области удаления

    def _erase_pen(self, sel: bool) -> QPen:
        return QPen(SEL if sel else QColor(214, 69, 47), 0, Qt.SolidLine if sel else Qt.DashLine)

    def _update_erase_items(self) -> None:
        for e in self.erase_items:
            if e.scene():
                self.scene().removeItem(e)
        self.erase_items = []
        if self.mode != "erase":
            return
        for pid in self.order:
            page = self.items_by_pid[pid]
            for o in self.model.page_objs(pid):
                if o["t"] != "erase":
                    continue
                r = fitz.Rect(o["rect"]) * page.D
                it = QGraphicsRectItem(QRectF(r.x0, r.y0, r.width, r.height), page)
                it.setData(0, o["id"])
                it.setPen(self._erase_pen(o["id"] == self.selected))
                it.setBrush(QColor(214, 69, 47, 25))
                it.setZValue(15)
                self.erase_items.append(it)

    # ------------------------------------------------------------ строки текста

    def line_poly(self, page: PageItem, ln) -> QPolygonF:
        return QPolygonF([page.to_disp(x, y) for x, y in ln["quad"]])

    def line_at(self, page: PageItem, pos: QPointF):
        lines = self.tab.lines_cached(page.pid)
        if lines is None:
            self.tab.prefetch_lines([page.pid])
            return None
        best = None
        for ln in lines:
            poly = self.line_poly(page, ln)
            if poly.containsPoint(pos, Qt.OddEvenFill) or \
                    poly.boundingRect().adjusted(-0.5, -1.5, 0.5, 1.5).contains(pos):
                area = poly.boundingRect().width() * poly.boundingRect().height()
                if best is None or area < best[0]:
                    best = (area, ln)
        return best[1] if best else None

    # ------------------------------------------------------------ мышь

    def _page_at(self, view_pos) -> tuple[PageItem | None, QPointF]:
        sp = self.mapToScene(view_pos)
        for pid in self.order:
            it = self.items_by_pid[pid]
            lp = it.mapFromScene(sp)
            if 0 <= lp.x() <= it.w and 0 <= lp.y() <= it.h:
                return it, lp
        return None, QPointF()

    def mousePressEvent(self, e) -> None:
        if self.editing is not None:
            ed = self.editing.editor
            item = self.itemAt(e.position().toPoint())
            if ed is not None and (item is ed or (item is not None and item.parentItem() is ed)):
                super().mousePressEvent(e)
                return
            self.finish_edit(True)
        if e.button() == Qt.RightButton:
            super().mousePressEvent(e)
            return
        item = self.itemAt(e.position().toPoint())
        if isinstance(item, ObjItem) and self.mode not in ("erase", "measure"):
            super().mousePressEvent(e)
            return
        page, lp = self._page_at(e.position().toPoint())
        if e.button() != Qt.LeftButton:
            return super().mousePressEvent(e)
        if page is None:
            self.select(None)
            if self.mode == "select":
                self._clear_tsel()
                self._start_pan(e)
            return
        if self.mode == "edit":
            ln = self.line_at(page, lp)
            if ln is not None:
                self.start_line_edit(page, ln, lp)
            else:
                self.select(None)
                if self.tab.lines_cached(page.pid) == []:
                    self.tab.toast("На этом листе нет текстового слоя (похоже на скан). Используйте «Удалить "
                                   "область» и «Добавить текст».")
            return
        if self.mode == "addtext":
            self.start_new_text(page, lp)
            return
        if self.mode == "stamp":
            if self.tab.picked_asset() is not None:
                self.tab.place_stamp(page, lp)
            else:
                self.select(None)
                self.tab.toast("Выберите подпись или печать справа, затем кликните на лист")
            return
        if self.mode == "erase":
            hit = None
            for it in self.erase_items:
                if it.parentItem() is page and it.rect().contains(lp):
                    hit = it.data(0)
            if hit:
                self.select(hit)
                return
            self.select(None)
            self.rubber_start = (page, lp)
            self.rubber = QGraphicsRectItem(QRectF(lp, lp), page)
            self.rubber.setPen(QPen(QColor(214, 69, 47), 0, Qt.DashLine))
            self.rubber.setBrush(QColor(214, 69, 47, 30))
            self.rubber.setZValue(30)
            return
        if self.mode == "measure":
            self.tab.measure_click(page, lp, e)
            return
        # «Выбор»: по тексту — выделение для копирования, на пустом месте — тянуть лист
        self.select(None)
        lines = self.tab.view_lines(page.pid)
        hit = self._sel_hit(page, lp, lines) if lines else None
        if hit is not None:
            if e.modifiers() & Qt.ShiftModifier and self.tsel and self.tsel["pid"] == page.pid:
                self.tsel["focus"] = hit
            else:
                self.tsel = {"pid": page.pid, "lines": lines, "anchor": hit, "focus": hit}
            self._tsel_drag = True
            self._draw_tsel()
            return
        self._clear_tsel()
        self._start_pan(e)

    def _start_pan(self, e) -> None:
        self._pan = (e.position(), self.horizontalScrollBar().value(), self.verticalScrollBar().value())
        self.viewport().setCursor(Qt.ClosedHandCursor)

    def mouseDoubleClickEvent(self, e) -> None:
        if self.mode == "measure":
            self.tab.measure_finish()
            return
        if self.mode == "select" and self.editing is None:
            item = self.itemAt(e.position().toPoint())
            if not isinstance(item, ObjItem):
                page, lp = self._page_at(e.position().toPoint())
                lines = self.tab.view_lines(page.pid) if page else None
                hit = self._sel_hit(page, lp, lines) if lines else None
                if hit is not None:  # двойной клик — слово
                    li, ci = hit
                    t = lines[li]["text"]
                    a = min(ci, len(t) - 1) if t else 0
                    while a > 0 and not t[a - 1].isspace():
                        a -= 1
                    b = ci
                    while b < len(t) and not t[b].isspace():
                        b += 1
                    self.tsel = {"pid": page.pid, "lines": lines, "anchor": (li, a), "focus": (li, b)}
                    self._draw_tsel()
                    return
        super().mouseDoubleClickEvent(e)

    # ------------------------------------------------------------ выделение текста

    tsel = None
    _tsel_drag = False
    _pan = None
    _tsel_items: list = []

    def _sel_hit(self, page, lp, lines, nearest: bool = False):
        best = None
        for li, ln in enumerate(lines):
            poly = self.line_poly(page, ln)
            br = poly.boundingRect()
            if poly.containsPoint(lp, Qt.OddEvenFill) or br.adjusted(-1, -2, 1, 2).contains(lp):
                return li, self._char_index_at(page, ln, ln["text"], lp)
            if nearest:
                dx = max(br.left() - lp.x(), 0.0, lp.x() - br.right())
                dy = max(br.top() - lp.y(), 0.0, lp.y() - br.bottom())
                dist = dx * 0.25 + dy
                if best is None or dist < best[0]:
                    best = (dist, li)
        if best is not None:
            ln = lines[best[1]]
            return best[1], self._char_index_at(page, ln, ln["text"], lp)
        return None

    def _tsel_range(self):
        t = self.tsel
        a, b = sorted([t["anchor"], t["focus"]])
        return a, b

    def selected_text(self) -> str:
        if not self.tsel:
            return ""
        (la, ca), (lb, cb) = self._tsel_range()
        out = []
        for li in range(la, lb + 1):
            t = self.tsel["lines"][li]["text"]
            s0 = ca if li == la else 0
            s1 = cb if li == lb else len(t)
            out.append(t[s0:s1])
        return "\n".join(out)

    def _clear_tsel(self) -> None:
        for it in self._tsel_items:
            if it.scene():
                it.scene().removeItem(it)
        self._tsel_items = []
        self.tsel = None
        self._tsel_drag = False

    def _draw_tsel(self) -> None:
        for it in self._tsel_items:
            if it.scene():
                it.scene().removeItem(it)
        self._tsel_items = []
        if not self.tsel:
            return
        page = self.items_by_pid.get(self.tsel["pid"])
        if page is None:
            return
        (la, ca), (lb, cb) = self._tsel_range()
        for li in range(la, lb + 1):
            ln = self.tsel["lines"][li]
            n = len(ln["chars"])
            s0 = ca if li == la else 0
            s1 = cb if li == lb else n
            if s1 <= s0:
                continue
            r = fitz.Rect()
            for c in ln["chars"][s0:s1]:
                r |= fitz.Rect(c[3], c[4], c[5], c[6])
            poly = QPolygonF([page.to_disp(r.x0, r.y0), page.to_disp(r.x1, r.y0), page.to_disp(r.x1, r.y1),
                              page.to_disp(r.x0, r.y1)])
            g = QGraphicsPolygonItem(poly, page)
            g.setBrush(QColor(47, 111, 214, 70))
            g.setPen(Qt.NoPen)
            g.setZValue(11)
            self._tsel_items.append(g)
        self.selectionChanged.emit()

    def copy_selection(self) -> bool:
        txt = self.selected_text()
        if txt:
            QApplication.clipboard().setText(txt)
            self.tab.toast(f"Скопировано символов: {len(txt)}")
            return True
        return False

    def select_all_text(self) -> None:
        it = self.page_item_at_index(self._cur_page)
        if it is None:
            return
        lines = self.tab.view_lines(it.pid)
        if lines:
            self.tsel = {"pid": it.pid, "lines": lines, "anchor": (0, 0),
                         "focus": (len(lines) - 1, len(lines[-1]["text"]))}
            self._draw_tsel()

    def mouseMoveEvent(self, e) -> None:
        if self._pan is not None:
            p0, h0, v0 = self._pan
            dlt = e.position() - p0
            self.horizontalScrollBar().setValue(int(h0 - dlt.x()))
            self.verticalScrollBar().setValue(int(v0 - dlt.y()))
            return
        if self._tsel_drag and self.tsel:
            page = self.items_by_pid.get(self.tsel["pid"])
            if page is not None:
                lp = page.mapFromScene(self.mapToScene(e.position().toPoint()))
                hit = self._sel_hit(page, lp, self.tsel["lines"], nearest=True)
                if hit is not None and hit != self.tsel["focus"]:
                    self.tsel["focus"] = hit
                    self._draw_tsel()
            return
        if self.mode == "measure":
            page, lp = self._page_at(e.position().toPoint())
            self.tab.measure_move(page, lp, e)
        if self.rubber is not None:
            page, start = self.rubber_start
            lp = page.mapFromScene(self.mapToScene(e.position().toPoint()))
            lp = QPointF(min(max(lp.x(), 0), page.w), min(max(lp.y(), 0), page.h))
            self.rubber.setRect(QRectF(start, lp).normalized())
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e) -> None:
        if self._pan is not None:
            self._pan = None
            self.viewport().setCursor(self.mode_cursor())
            return
        if self._tsel_drag:
            self._tsel_drag = False
            return
        if self.rubber is not None:
            page, _ = self.rubber_start
            r = self.rubber.rect()
            self.scene().removeItem(self.rubber)
            self.rubber = None
            if r.width() > 2 and r.height() > 2:
                rr = fitz.Rect(r.left(), r.top(), r.right(), r.bottom()) * page.Dinv
                o = self.model.add_obj(page.pid, {"t": "erase", "rect": [rr.x0, rr.y0, rr.x1, rr.y1], "white": self.tab.erase_white_default})
                self.tab.after_change(pages=[page.pid])
                self.select(o["id"])
            return
        super().mouseReleaseEvent(e)

    def contextMenuEvent(self, e) -> None:
        page, lp = self._page_at(e.pos())
        if page is None:
            return
        item = self.itemAt(e.pos())
        m = QMenu(self)
        if isinstance(item, ObjItem):
            self.select(item.oid)
            o = item.obj
            if o["t"] == "image":
                m.addAction("Поставить на все листы", lambda: self.tab.stamp_to_pages("all"))
                m.addAction("Поставить на выбранные листы", lambda: self.tab.stamp_to_pages("selected"))
                m.addAction("Повернуть на 90°", lambda: self.tab.rotate_selected_obj(90))
            if o["t"] == "text":
                m.addAction("Изменить текст", lambda: self.start_text_obj_edit(page, item.oid))
            m.addAction("Удалить", self.delete_selected)
            m.addSeparator()
        idx = self.order.index(page.pid)
        m.addAction("Повернуть лист по часовой", lambda: self.tab.rotate_pages([page.pid], 90))
        m.addAction("Повернуть лист против часовой", lambda: self.tab.rotate_pages([page.pid], 270))
        m.addAction("Вставить пустой лист после", lambda: self.tab.insert_blank(idx + 1))
        m.addAction("Вставить файл после…", lambda: self.tab.insert_files_dialog(idx + 1))
        m.addSeparator()
        m.addAction("Удалить лист", lambda: self.tab.delete_pages([page.pid]))
        m.exec(e.globalPos())

    def keyPressEvent(self, e) -> None:
        if self.editing is not None:
            super().keyPressEvent(e)
            return
        if e.key() in (Qt.Key_Delete, Qt.Key_Backspace) and self.selected:
            self.delete_selected()
            return
        if e.key() in (Qt.Key_Left, Qt.Key_Right, Qt.Key_Up, Qt.Key_Down) and self.selected:
            pid, o = self.model.find_obj(self.selected)
            if o is not None and o["t"] in ("image", "text"):
                step = 10.0 if e.modifiers() & Qt.ShiftModifier else 1.0
                dx = {Qt.Key_Left: -step, Qt.Key_Right: step}.get(e.key(), 0.0)
                dy = {Qt.Key_Up: -step, Qt.Key_Down: step}.get(e.key(), 0.0)
                page = self.items_by_pid[pid]
                kx, ky = ("cx", "cy") if o["t"] == "image" else ("x", "y")
                c = page.to_disp(o[kx], o[ky]) + QPointF(dx, dy)
                o[kx], o[ky] = page.to_unrot(c)
                self.model.commit()
                self.tab.after_change(pages=[pid])
                return
        if self.mode == "measure" and e.key() in (Qt.Key_Return, Qt.Key_Enter):
            self.tab.measure_finish()
            return
        if self.mode == "measure" and e.key() == Qt.Key_Escape and self.tab.measure.pts:
            self.tab.measure.cancel()
            return
        if e.key() == Qt.Key_Escape:
            if self.selected:
                self.select(None)
            elif self.mode != "select":
                self.tab.ctx.request_mode("select")
            return
        if e.key() in (Qt.Key_PageDown, Qt.Key_Space) and not e.modifiers():
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() + int(self.viewport().height() * 0.9))
            return
        if e.key() == Qt.Key_PageUp:
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - int(self.viewport().height() * 0.9))
            return
        super().keyPressEvent(e)

    # ------------------------------------------------------------ правка текста

    def start_line_edit(self, page: PageItem, ln: dict, click: QPointF | None = None) -> None:
        pid = page.pid
        existing = self.model.edit_for_line(pid, line_key(ln))
        if existing is not None:
            obj = json.loads(json.dumps(existing))
            is_new = False
        else:
            obj = {"t": "edit", "id": self.model_new_id(), "lines": [ln], "text": ln["text"], "style": None}
            is_new = True
        kind = "para" if obj.get("para") else "line"
        self._open_editor(page, obj, is_new, kind, click)

    def model_new_id(self) -> str:
        from ..core.model import new_id
        return new_id("o")

    def start_new_text(self, page: PageItem, lp: QPointF) -> None:
        st = self.tab.last_text_style
        size = float(st["size"])
        top_left = lp - QPointF(0, size * 0.75)
        x, y = page.to_unrot(top_left)
        ang = self.model.upright_angle(page.ref)
        obj = {"t": "text", "id": self.model_new_id(), "x": x, "y": y, "angle": ang, "text": "",
               "family": st["family"], "size": size, "bold": st["bold"], "italic": st["italic"],
               "color": st["color"]}
        self._open_editor(page, obj, True, "text", None)

    def start_text_obj_edit(self, page: PageItem, oid: str) -> None:
        pid, o = self.model.find_obj(oid)
        if o is None:
            return
        self.select(None)
        self._open_editor(page, json.loads(json.dumps(o)), False, "text", None)

    def editor_style(self, sess: EditSession) -> dict:
        """Шрифт/размер/цвет для редактора на месте — как будет в файле."""
        o = sess.obj
        if sess.kind == "text":
            st = B.text_obj_style(o)
            return {"file": st.fontfile, "size": st.size, "color": o.get("color", "#000000"),
                    "slant": B.F.font_synthetic_slant(st.family, st.italic)}
        ln = o["lines"][0]
        si = ln["chars"][0][7]
        span = ln["spans"][si]
        style = o.get("style") or {}
        size = float(style.get("size") or span["size"])
        from ..core.textlines import color_int_to_rgb, rgb_to_hex
        color = style.get("color") or rgb_to_hex(color_int_to_rgb(span["color"]))
        fam = style.get("family") or "orig"
        bold = style.get("bold", F.guess_bold(span["font"], span["flags"]))
        italic = style.get("italic", F.guess_italic(span["font"], span["flags"]))
        if fam == "orig":
            path = F.exact_font_file(span["font"], (16 if bold else 0) | (2 if italic else 0)) or \
                F.substitute_file(span["font"], 0, o.get("text", ""), bold=bold, italic=italic)
        else:
            path = F.substitute_file("", 0, o.get("text", ""), family=fam, bold=bold, italic=italic)
        return {"file": path, "size": size, "color": color, "slant": False}

    def _open_editor(self, page: PageItem, obj: dict, is_new: bool, kind: str, click) -> None:
        self.finish_edit(True)
        self.select(None)
        sess = EditSession(page, obj, is_new, kind)
        self.editing = sess
        if page.hover_poly is not None:
            page.hover_poly.setVisible(False)
        if not is_new and obj["id"] in self.obj_items:
            self.obj_items[obj["id"]].setVisible(False)
        # маска поверх старого текста, пока не придёт рендер без него
        if kind in ("line", "para"):
            for ln in obj["lines"]:
                poly = self.line_poly(page, ln)
                m = QGraphicsPolygonItem(poly, page)
                m.setBrush(page.background_at(poly))
                m.setPen(Qt.NoPen)
                m.setZValue(10)
                sess.masks.append(m)
        ed = InlineEditor(page, multiline=(kind != "line"), on_finish=lambda c, s=sess: self._editor_done(s, c))
        sess.editor = ed
        self._style_editor(sess)
        ed.setPlainText(obj.get("text", ""))
        self._place_editor(sess)
        ed.document().contentsChanged.connect(lambda s=sess: self._editor_changed(s))
        ed.setFocus(Qt.MouseFocusReason)
        self.setFocus()
        ed.setFocus()
        cur = ed.textCursor()
        if kind == "line" and click is not None:
            cur.setPosition(self._char_index_at(page, obj["lines"][0], obj.get("text", ""), click))
        else:
            cur.movePosition(QTextCursor.End)
        ed.setTextCursor(cur)
        page.clear_ghosts()
        self.editingChanged.emit()
        self.selectionChanged.emit()
        self.schedule_render()

    def _char_index_at(self, page, ln, text, click: QPointF) -> int:
        if text != ln["text"]:
            return len(text)
        x, y = page.to_unrot(click)
        d = ln["dir"]
        c0 = ln["chars"][0]
        s = (x - c0[1]) * d[0] + (y - c0[2]) * d[1]
        for i, c in enumerate(ln["chars"]):
            ci = (c[1] - c0[1]) * d[0] + (c[2] - c0[2]) * d[1]
            ce = max((px - c0[1]) * d[0] + (py - c0[2]) * d[1] for px, py in ((c[3], c[4]), (c[5], c[6])))
            if s < (ci + ce) / 2:
                return i
        return len(text)

    def _style_editor(self, sess: EditSession) -> None:
        st = self.editor_style(sess)
        ed = sess.editor
        fam = qtfonts.family_for_file(st["file"])
        f = QFont(fam)
        f.setPointSizeF(st["size"] * qtfonts.pt_factor())
        f.setKerning(False)
        if st.get("slant"):
            f.setItalic(True)
        ed.setFont(f)
        ed.setDefaultTextColor(QColor(st["color"]))
        sess.style_info = st

    def _place_editor(self, sess: EditSession) -> None:
        ed = sess.editor
        page = sess.page
        o = sess.obj
        fm = QFontMetricsF(ed.font())
        asc = fm.ascent()
        if sess.kind == "text":
            ed.setRotation(page.disp_angle(o.get("angle", 0)))
            ed.setPos(page.to_disp(o["x"], o["y"]))
            ed.setTextWidth(-1)
            return
        if sess.kind == "para":
            g = B.para_geometry(o["lines"])
            d = g["dir"]
            O = g["origin"]
            ox = O[0] + d[0] * g["left"]
            oy = O[1] + d[1] * g["left"]
            ed.setTextWidth(max(20.0, g["right"] - g["left"]))
        else:
            c0 = o["lines"][0]["chars"][0]
            d = o["lines"][0]["dir"]
            ox, oy = c0[1], c0[2]
            ed.setTextWidth(-1)
        ang = page.dir_angle(d)
        base = page.to_disp(ox, oy)
        a = math.radians(ang)
        # от базовой линии вверх на ascent (в повёрнутой системе)
        ed.setRotation(ang)
        ed.setPos(base + QPointF(math.sin(a) * asc, -math.cos(a) * asc))

    def set_editor_size(self, size: float | None) -> None:
        """Временный кегль редактора (режим «уместить в прежние строки»)."""
        sess = self.editing
        if sess is None or sess.editor is None:
            return
        f = sess.editor.font()
        want = (size if size else sess.style_info["size"]) * qtfonts.pt_factor()
        if abs(f.pointSizeF() - want) > 0.01:
            f.setPointSizeF(want)
            sess.editor.setFont(f)
            self._place_editor(sess)

    def _editor_changed(self, sess: EditSession) -> None:
        if sess.kind == "para":
            self.tab.schedule_para_hint()
        if sess.kind == "text" and sess.editor is not None:
            sess.obj["text"] = sess.editor.toPlainText()
        elif sess.kind == "line" and sess.editor is not None and not getattr(sess, "_autowrap", False):
            # строка перестала помещаться до края колонки/ячейки — переносим абзацем
            ln = sess.obj["lines"][0]
            lim = ln.get("limit")
            if lim and sess.editor.document().idealWidth() > lim + 1.0:
                sess._autowrap = True
                QTimer.singleShot(0, lambda s=sess: self._auto_paragraph(s))

    def _auto_paragraph(self, sess: EditSession) -> None:
        if self.editing is not sess or sess.kind != "line":
            return
        pos = sess.editor.textCursor().position()
        self.toggle_paragraph(cursor_in_line=pos)
        self.tab.toast("Текст не помещается в строку — переношу по ширине абзаца")

    def _editor_done(self, sess: EditSession, commit: bool) -> None:
        if self.editing is not sess:
            return
        page = sess.page
        ed = sess.editor
        text = ed.toPlainText()
        if sess.kind == "line":
            text = text.replace("\n", " ")
        o = sess.obj
        changed = False
        if commit:
            if sess.kind == "text":
                if not text.strip():
                    if not sess.is_new:
                        self.model.remove_obj(o["id"])
                        changed = True
                else:
                    o["text"] = text
                    if sess.is_new:
                        self.model.add_obj(sess.pid, o)
                        changed = True
                    else:
                        cur = self.model.find_obj(o["id"])[1]
                        if cur is not None and cur != o:
                            cur.clear()
                            cur.update(o)
                            self.model.commit()
                            changed = True
            else:
                o["text"] = text
                orig_join = join_para_text([ln["text"] for ln in o["lines"]]) if o.get("para") else o["lines"][0]["text"]
                untouched = text == orig_join and not o.get("style")
                if sess.is_new:
                    if not untouched:
                        self.model.add_obj(sess.pid, o)
                        changed = True
                else:
                    cur = self.model.find_obj(o["id"])[1]
                    if untouched:
                        self.model.remove_obj(o["id"])
                        changed = True
                    elif cur is not None and cur != o:
                        cur.clear()
                        cur.update(o)
                        self.model.commit()
                        changed = True
        self.editing = None
        # редактор остаётся «призраком», пока не придёт новый рендер
        ed.setTextInteractionFlags(Qt.NoTextInteraction)
        page.clear_ghosts()
        page.ghosts = [ed] + sess.masks
        self.tab.after_change(pages=[sess.pid], quiet=not changed)
        if page.base_key == page.content_key():
            page.clear_ghosts()
        self.editingChanged.emit()
        self.selectionChanged.emit()

    def finish_edit(self, commit: bool = True) -> None:
        if self.editing is not None and self.editing.editor is not None:
            self.editing.editor.finish(commit)

    def apply_style_to_edit(self, **style) -> None:
        """Изменение шрифта/размера/цвета из панели свойств во время правки."""
        sess = self.editing
        if sess is None:
            return
        o = sess.obj
        if sess.kind == "text":
            o.update(style)
        else:
            st = dict(o.get("style") or {})
            st.update(style)
            o["style"] = st
        self._style_editor(sess)
        self._place_editor(sess)
        sess.editor.setFocus()
        self.schedule_render()

    def toggle_paragraph(self, cursor_in_line: int | None = None) -> None:
        sess = self.editing
        if sess is None or sess.kind not in ("line", "para"):
            return
        page = sess.page
        o = sess.obj
        cur_text = sess.editor.toPlainText().replace("\n", " ")
        cursor_at = None
        if sess.kind == "line":
            lines = self.tab.lines_cached(page.pid) or []
            para = paragraph_of(lines, o["lines"][0]) or [o["lines"][0]]
            if self.model.find_obj(o["id"])[1] is not None:
                self.model.remove_obj(o["id"], commit=False)
            # строки абзаца, уже правленные по отдельности, включаем в абзац
            texts = []
            k_cur = 0
            for ln in para:
                if line_key(ln) == line_key(o["lines"][0]):
                    k_cur = len(texts)
                    texts.append(cur_text)
                    continue
                other = self.model.edit_for_line(page.pid, line_key(ln))
                if other is not None and not other.get("para"):
                    texts.append(other["text"])
                    self.model.remove_obj(other["id"], commit=False)
                else:
                    texts.append(ln["text"])
            o["lines"] = para
            o["para"] = True
            o["text"] = join_para_text(texts)
            before = join_para_text(texts[:k_cur])
            cursor_at = (len(before) + (1 if before else 0) + cursor_in_line) if cursor_in_line is not None else None
            sess.is_new = True
            sess.kind = "para"
        else:
            return
        for m in sess.masks:
            if m.scene():
                m.scene().removeItem(m)
        sess.masks = []
        for ln in o["lines"]:
            poly = self.line_poly(page, ln)
            mk = QGraphicsPolygonItem(poly, page)
            mk.setBrush(page.background_at(poly))
            mk.setPen(Qt.NoPen)
            mk.setZValue(10)
            sess.masks.append(mk)
        sess.editor.multiline = True
        opt = sess.editor.document().defaultTextOption()
        opt.setWrapMode(QTextOption.WordWrap)
        sess.editor.document().setDefaultTextOption(opt)
        sess.editor.setPlainText(o["text"])
        self._place_editor(sess)
        sess.editor.setFocus()
        c = sess.editor.textCursor()
        if cursor_at is not None:
            c.setPosition(max(0, min(cursor_at, len(o["text"]))))
        else:
            c.movePosition(QTextCursor.End)
        sess.editor.setTextCursor(c)
        self.editingChanged.emit()
        self.schedule_render()

    def revert_edit(self) -> None:
        """Вернуть исходный текст строки/абзаца."""
        sess = self.editing
        if sess is None or sess.kind == "text":
            return
        oid = sess.obj["id"]
        sess.editor.blockSignals(True)
        self.editing = None
        sess.editor.finish(False)
        page = sess.page
        for g in [sess.editor] + sess.masks:
            if g.scene():
                g.scene().removeItem(g)
        if self.model.find_obj(oid)[1] is not None:
            self.model.remove_obj(oid, commit=False)
        # (при переходе к абзацу правки отдельных строк уже убраны — фиксируем это одним шагом)
        self.model.commit()
        self.tab.after_change(pages=[page.pid])
        self.editingChanged.emit()

    # ------------------------------------------------------------ поиск

    def show_search(self, results: dict, current: tuple | None) -> None:
        for it in self.items_by_pid.values():
            for s in it.search_items:
                if s.scene():
                    s.scene().removeItem(s)
            it.search_items = []
        for pid, quads in results.items():
            page = self.items_by_pid.get(pid)
            if page is None:
                continue
            for qi, q in enumerate(quads):
                poly = QPolygonF([page.to_disp(x, y) for x, y in q])
                g = QGraphicsPolygonItem(poly, page)
                cur = current is not None and current == (pid, qi)
                g.setBrush(QColor(255, 140, 0, 120) if cur else QColor(255, 220, 0, 110))
                g.setPen(Qt.NoPen)
                g.setZValue(12)
                page.search_items.append(g)
                if cur:
                    self.ensure_visible_rect(page, poly.boundingRect())
