"""Миниатюры листов: колонка слева и режим «Систематизировать листы» (сетка, как в Acrobat).

Перетаскивание меняет порядок, ⌘/⇧-клик — выбор нескольких, файлы из Finder
вставляются туда, куда их бросили. В сетке при наведении — кнопки поворота и
удаления, между листами — «+» для вставки.
"""
from __future__ import annotations

import hashlib
import json

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QListView, QListWidget, QListWidgetItem, QMenu, QStyle,
                               QStyledItemDelegate)

from ..core.ops import page_format
from . import icons

PID = Qt.UserRole + 1
LABEL_H = 18
BTN = 26  # кнопки на миниатюре в сетке


class _Delegate(QStyledItemDelegate):
    def __init__(self, panel: "ThumbPanel"):
        super().__init__(panel)
        self.panel = panel

    def _k(self, w, h) -> float:
        tw = self.panel.thumb_w
        return min(tw / max(1, w), tw * 1.3 / max(1, h))

    def sizeHint(self, opt, idx) -> QSize:
        tw = self.panel.thumb_w
        if self.panel.grid:  # в сетке все ячейки одной высоты — ровные ряды
            return QSize(tw + 28, int(tw * 1.3) + LABEL_H * 2 + 20)
        w, h = self.panel.page_size(idx.data(PID))
        return QSize(tw + 28, int(h * self._k(w, h)) + LABEL_H + 18)

    def thumb_rect(self, r: QRect, pid) -> QRectF:
        w, h = self.panel.page_size(pid)
        k = self._k(w, h)
        tw, th = w * k, h * k
        x = r.x() + (r.width() - tw) / 2
        if self.panel.grid:
            y = r.y() + 8 + (self.panel.thumb_w * 1.3 - th) / 2
        else:
            y = r.y() + 8
        return QRectF(x, y, tw, th)

    def buttons(self, r: QRect, pid) -> dict:
        box = self.thumb_rect(r, pid)
        cx = box.center().x()
        y = box.top() + 6
        return {"rotl": QRectF(cx - 1.5 * BTN - 4, y, BTN, BTN), "rot": QRectF(cx - BTN / 2, y, BTN, BTN),
                "delete": QRectF(cx + BTN / 2 + 4, y, BTN, BTN)}

    def paint(self, p: QPainter, opt, idx) -> None:
        pid = idx.data(PID)
        r = opt.rect
        sel = bool(opt.state & QStyle.State_Selected)
        hover = bool(opt.state & QStyle.State_MouseOver)
        cur = pid == self.panel.current_pid
        box = self.thumb_rect(r, pid)
        p.save()
        p.setRenderHint(QPainter.Antialiasing, True)
        if sel:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(47, 111, 214, 40))
            p.drawRoundedRect(QRectF(r).adjusted(4, 2, -4, -2), 8, 8)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, 30))
        p.drawRect(box.adjusted(-1, 0, 1, 2))
        p.setBrush(Qt.white)
        p.drawRect(box)
        pm = self.panel.pixmap(pid)
        if pm is not None:
            p.setRenderHint(QPainter.SmoothPixmapTransform, True)
            p.drawPixmap(box, pm, QRectF(pm.rect()))
        else:
            self.panel.request(pid)
        if sel or cur:
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(QColor(47, 111, 214) if sel else QColor(140, 150, 165), 2.4 if sel else 1.4))
            p.drawRect(box.adjusted(-1.5, -1.5, 1.5, 1.5))
        if self.panel.grid and hover:
            for name, br in self.buttons(r, pid).items():
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(29, 35, 48, 215))
                p.drawRoundedRect(br, 6, 6)
                ic = icons.icon({"rotl": "rotateleft", "rot": "rotate", "delete": "trash"}[name], white=True)
                ic.paint(p, br.adjusted(4, 4, -4, -4).toRect())
        num = idx.row() + 1
        f = QFont(opt.font)
        f.setPointSizeF(max(9.0, f.pointSizeF() - 1))
        f.setBold(cur)
        p.setFont(f)
        p.setPen(opt.palette.color(opt.palette.ColorRole.Text))
        ty = int(r.y() + 8 + self.panel.thumb_w * 1.3 + 4) if self.panel.grid else int(box.bottom() + 3)
        p.drawText(QRect(r.x(), ty, r.width(), LABEL_H), Qt.AlignCenter, str(num))
        if self.panel.grid:
            f.setBold(False)
            f.setPointSizeF(max(8.5, f.pointSizeF() - 1))
            p.setFont(f)
            p.setPen(opt.palette.color(opt.palette.ColorRole.PlaceholderText))
            w, h = self.panel.page_size(pid)
            p.drawText(QRect(r.x(), ty + LABEL_H - 2, r.width(), LABEL_H), Qt.AlignCenter, page_format(w, h))
        p.restore()


class ThumbPanel(QListWidget):
    orderChanged = Signal(list)              # новые pid по порядку
    pageActivated = Signal(int)
    pageOpened = Signal(int)                 # двойной клик в сетке
    filesDropped = Signal(list, int)         # пути, позиция вставки
    selectionPids = Signal(list)
    contextAction = Signal(str, list)        # действие, выбранные pid
    insertAt = Signal(int, QPoint)           # «+» между листами: позиция, где показать меню

    def __init__(self, tab, grid: bool = False):
        super().__init__()
        self.tab = tab
        self.grid = grid
        self.thumb_w = 170 if grid else 132
        self.current_pid = None
        self._pix: dict = {}
        self._pending: set = set()
        self._gap: int | None = None  # позиция «+» для вставки между листами
        self.setItemDelegate(_Delegate(self))
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setDragDropMode(QAbstractItemView.InternalMove)
        self.setDefaultDropAction(Qt.MoveAction)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setUniformItemSizes(grid)
        self.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.setFrameShape(QListWidget.NoFrame)
        self.setMouseTracking(True)
        if grid:
            self.setObjectName("pageGrid")
            self.setFlow(QListView.LeftToRight)
            self.setWrapping(True)
            self.setResizeMode(QListView.Adjust)
            self.setSpacing(14)
            self.setViewportMargins(10, 10, 10, 10)
        else:
            self.setObjectName("thumbs")
            self.setSpacing(2)
            self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            self.setMinimumWidth(self.thumb_w + 40)
            self.setMaximumWidth(self.thumb_w + 60)
        self.itemClicked.connect(lambda it: self.pageActivated.emit(self.row(it)))
        self.itemDoubleClicked.connect(lambda it: self.pageOpened.emit(self.row(it)))
        self.itemSelectionChanged.connect(lambda: self.selectionPids.emit(self.selected_pids()))
        self.model().rowsMoved.connect(lambda *a: QTimer.singleShot(0, self._emit_order))
        self._building = False

    def set_thumb_width(self, w: int) -> None:
        self.thumb_w = int(w)
        self._pix.clear()
        self.scheduleDelayedItemsLayout()
        self.viewport().update()

    # ------------------------------------------------------------ данные

    def page_size(self, pid):
        ref = self.tab.model.page_ref(pid)
        if ref is None:
            return 595, 842
        return self.tab.model.display_size(ref)

    def thumb_key(self, pid):
        ref = self.tab.model.page_ref(pid)
        objs = [o for o in self.tab.model.page_objs(pid) if o.get("t") != "image"]
        h = hashlib.sha1(json.dumps(objs, sort_keys=True, ensure_ascii=False).encode()).hexdigest() if objs else ""
        imgs = tuple((o["asset"], round(o["cx"]), round(o["cy"]), round(o["w"]), round(o.get("angle", 0)))
                     for o in self.tab.model.page_objs(pid) if o.get("t") == "image")
        return (ref["src"], ref["idx"], ref["rot"], h, imgs, self.thumb_w)

    def pixmap(self, pid):
        ent = self._pix.get(pid)
        if ent is None:
            return None
        key, pm = ent
        if key != self.thumb_key(pid):
            self.request(pid)
        return pm

    def request(self, pid) -> None:
        if pid in self._pending:
            return
        ref = self.tab.model.page_ref(pid)
        if ref is None:
            return
        key = self.thumb_key(pid)
        self._pending.add(pid)
        w, h = self.page_size(pid)
        dpr = self.devicePixelRatioF()
        scale = min(self.thumb_w / max(1, w), self.thumb_w * 1.3 / max(1, h)) * dpr
        objs = [o for o in self.tab.model.page_objs(pid) if o.get("t") != "image"]
        images = [o for o in self.tab.model.page_objs(pid) if o.get("t") == "image"]

        def done(res, pid=pid, key=key, scale=scale, images=images):
            self._pending.discard(pid)
            _, _, w_, h_, x, y, data = res
            img = QImage(data, w_, h_, 3 * w_, QImage.Format_RGB888).copy()
            if images:  # печати поверх (как в просмотре — их рисует Qt)
                p = QPainter(img)
                p.setRenderHint(QPainter.SmoothPixmapTransform)
                page = self.tab.view.items_by_pid.get(pid)
                for o in images:
                    qi = self.tab.asset_image(o["asset"])
                    if qi is None or page is None:
                        continue
                    c = page.to_disp(o["cx"], o["cy"])
                    p.save()
                    p.scale(scale, scale)
                    p.translate(c)
                    p.rotate(page.disp_angle(o.get("angle", 0)))
                    p.drawImage(QRectF(-o["w"] / 2, -o["h"] / 2, o["w"], o["h"]), qi)
                    p.restore()
                p.end()
            self._pix[pid] = (key, QPixmap.fromImage(img))
            self.viewport().update()

        def fail(msg, pid=pid):
            self._pending.discard(pid)

        self.tab.service.submit("render", (ref["src"], ref["idx"], ref["rot"], objs, [], scale, None),
                                sids=[ref["src"]], priority=5 + self.tab.model.page_index(pid) * 0.001,
                                key=("thumb", id(self), pid), callback=done, errback=fail)

    # ------------------------------------------------------------ построение

    def rebuild(self) -> None:
        self._building = True
        sel = set(self.selected_pids())
        self.clear()
        for ref in self.tab.model.pages:
            it = QListWidgetItem()
            it.setData(PID, ref["id"])
            it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsDragEnabled)
            self.addItem(it)
            if ref["id"] in sel:
                it.setSelected(True)
        self._building = False
        live = {r["id"] for r in self.tab.model.pages}
        for pid in list(self._pix):
            if pid not in live:
                del self._pix[pid]
        self.viewport().update()

    def refresh(self) -> None:
        self.viewport().update()

    def set_current(self, i: int) -> None:
        it = self.item(i)
        self.current_pid = it.data(PID) if it else None
        if it and not self.grid:
            self.scrollToItem(it, QAbstractItemView.EnsureVisible)
        self.viewport().update()

    def selected_pids(self) -> list:
        rows = sorted(self.row(i) for i in self.selectedItems())
        return [self.item(r).data(PID) for r in rows]

    def select_pids(self, pids) -> None:
        pids = set(pids)
        for i in range(self.count()):
            self.item(i).setSelected(self.item(i).data(PID) in pids)

    def _emit_order(self) -> None:
        if self._building:
            return
        order = [self.item(i).data(PID) for i in range(self.count())]
        self.orderChanged.emit(order)

    # ------------------------------------------------------------ кнопки на миниатюрах и «+» между ними

    def _gap_at(self, pos: QPoint) -> int | None:
        """Если курсор в промежутке между соседними листами одного ряда — позиция вставки."""
        if not self.grid or self.count() == 0:
            return None
        for i in range(self.count()):
            r = self.visualItemRect(self.item(i))
            if r.top() <= pos.y() <= r.bottom():
                if abs(pos.x() - (r.left() - self.spacing() / 2)) <= self.spacing() / 2 + 3:
                    return i
                if i == self.count() - 1 or self.visualItemRect(self.item(i + 1)).top() > r.top():
                    if abs(pos.x() - (r.right() + self.spacing() / 2)) <= self.spacing() / 2 + 3:
                        return i + 1
        return None

    def mouseMoveEvent(self, e) -> None:
        if self.grid and not (e.buttons() & Qt.LeftButton):
            g = self._gap_at(e.position().toPoint())
            if g != self._gap:
                self._gap = g
                self.viewport().update()
        super().mouseMoveEvent(e)

    def leaveEvent(self, e) -> None:
        self._gap = None
        self.viewport().update()
        super().leaveEvent(e)

    def mousePressEvent(self, e) -> None:
        if self.grid and e.button() == Qt.LeftButton:
            pos = e.position().toPoint()
            g = self._gap_at(pos)
            if g is not None:
                self.insertAt.emit(g, self.viewport().mapToGlobal(pos))
                return
            it = self.itemAt(pos)
            if it is not None:
                for name, br in self.itemDelegate().buttons(self.visualItemRect(it), it.data(PID)).items():
                    if br.contains(QPointF(pos)):
                        pids = self.selected_pids() if it.isSelected() and len(self.selected_pids()) > 1 \
                            else [it.data(PID)]
                        self.contextAction.emit(name, pids)
                        return
        super().mousePressEvent(e)

    def paintEvent(self, e) -> None:
        super().paintEvent(e)
        if self.grid and self._gap is not None:
            i = self._gap
            if i < self.count():
                r = self.visualItemRect(self.item(i))
                x = r.left() - self.spacing() / 2
            else:
                r = self.visualItemRect(self.item(self.count() - 1))
                x = r.right() + self.spacing() / 2
            p = QPainter(self.viewport())
            p.setRenderHint(QPainter.Antialiasing)
            p.setPen(QPen(QColor(47, 111, 214), 2))
            p.drawLine(QPointF(x, r.top() + 10), QPointF(x, r.bottom() - 10))
            p.setBrush(QColor(47, 111, 214))
            c = QPointF(x, r.center().y())
            p.drawEllipse(c, 11, 11)
            p.setPen(QPen(Qt.white, 2.2))
            p.drawLine(c + QPointF(-5, 0), c + QPointF(5, 0))
            p.drawLine(c + QPointF(0, -5), c + QPointF(0, 5))
            p.end()

    # ------------------------------------------------------------ перетаскивание из Finder

    def dragEnterEvent(self, e) -> None:
        if e.mimeData().hasUrls() and e.source() is None:
            e.acceptProposedAction()
            return
        super().dragEnterEvent(e)

    def dragMoveEvent(self, e) -> None:
        if e.mimeData().hasUrls() and e.source() is None:
            e.acceptProposedAction()
            return
        super().dragMoveEvent(e)

    def dropEvent(self, e) -> None:
        if e.mimeData().hasUrls() and e.source() is None:
            paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
            it = self.itemAt(e.position().toPoint())
            pos = self.count()
            if it is not None:
                r = self.visualItemRect(it)
                after = e.position().x() > r.center().x() if self.grid else e.position().y() > r.center().y()
                pos = self.row(it) + (1 if after else 0)
            e.acceptProposedAction()
            if paths:
                self.filesDropped.emit(paths, pos)
            return
        super().dropEvent(e)

    def contextMenuEvent(self, e) -> None:
        it = self.itemAt(e.pos())
        if it is not None and not it.isSelected():
            self.clearSelection()
            it.setSelected(True)
        pids = self.selected_pids()
        if not pids:
            return
        n = len(pids)
        m = QMenu(self)
        m.addAction("Повернуть по часовой", lambda: self.contextAction.emit("rot", pids))
        m.addAction("Повернуть против часовой", lambda: self.contextAction.emit("rotl", pids))
        m.addSeparator()
        m.addAction("Вставить пустой лист перед", lambda: self.contextAction.emit("blank_before", pids))
        m.addAction("Вставить пустой лист после", lambda: self.contextAction.emit("blank_after", pids))
        m.addAction("Вставить файл после…", lambda: self.contextAction.emit("insert_after", pids))
        m.addSeparator()
        m.addAction(f"Извлечь в отдельный PDF ({n})…", lambda: self.contextAction.emit("extract", pids))
        m.addAction(f"Удалить ({n})", lambda: self.contextAction.emit("delete", pids))
        m.exec(e.globalPos())

    def keyPressEvent(self, e) -> None:
        if e.key() in (Qt.Key_Delete, Qt.Key_Backspace) and self.selected_pids():
            self.contextAction.emit("delete", self.selected_pids())
            return
        super().keyPressEvent(e)
