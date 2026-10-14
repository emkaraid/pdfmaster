"""Векторные иконки панели инструментов (рисуются QPainter, подходят под тёмную тему)."""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QIcon, QPainter, QPainterPath, QPalette, QPen, QPixmap

_cache: dict = {}


def _ink(disabled=False) -> QColor:
    pal = QGuiApplication.palette()
    c = pal.color(QPalette.Disabled if disabled else QPalette.Active, QPalette.WindowText)
    return c


def _draw(name: str, p: QPainter, c: QColor) -> None:
    pen = QPen(c, 1.6, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    P = QPointF
    if name == "open":
        path = QPainterPath(P(3, 7))
        path.lineTo(3, 19); path.lineTo(19, 19); path.lineTo(21, 10); path.lineTo(7, 10); path.lineTo(5, 15)
        p.drawPath(path)
        path = QPainterPath(P(3, 15)); path.lineTo(3, 5); path.lineTo(9, 5); path.lineTo(11, 7); path.lineTo(18, 7); path.lineTo(18, 10)
        p.drawPath(path)
    elif name == "save":
        p.drawRoundedRect(QRectF(4, 4, 16, 16), 2, 2)
        p.drawRect(QRectF(8, 4, 8, 5))
        p.drawRect(QRectF(7, 13, 10, 7))
    elif name == "saveas":
        p.drawRoundedRect(QRectF(3, 6, 14, 14), 2, 2)
        p.drawRect(QRectF(6, 6, 7, 4))
        path = QPainterPath(P(15, 4)); path.lineTo(21, 4); path.lineTo(21, 10)
        p.drawPath(path)
        p.drawLine(P(21, 4), P(14, 11))
    elif name == "grid":
        for x in (3, 13):
            for y in (3, 13):
                p.drawRect(QRectF(x, y, 8, 8))
    elif name == "ruler":
        p.save(); p.translate(12, 12); p.rotate(-35)
        p.drawRect(QRectF(-11, -4, 22, 8))
        for k in range(-8, 9, 3):
            p.drawLine(P(k, -4), P(k, -1 if k % 2 else 0))
        p.restore()
    elif name == "optimize":
        p.drawRect(QRectF(5, 3, 14, 18))
        p.drawLine(P(12, 7), P(12, 15)); p.drawLine(P(9, 12), P(12, 15)); p.drawLine(P(15, 12), P(12, 15))
        p.drawLine(P(8, 18), P(16, 18))
    elif name == "select":
        path = QPainterPath(P(6, 3)); path.lineTo(6, 19); path.lineTo(10, 15); path.lineTo(13, 21); path.lineTo(15, 20)
        path.lineTo(12, 14); path.lineTo(18, 14); path.closeSubpath()
        p.drawPath(path)
    elif name == "edittext":
        p.drawLine(P(4, 5), P(14, 5)); p.drawLine(P(9, 5), P(9, 18))
        p.drawLine(P(17, 8), P(17, 20)); p.drawLine(P(15, 8), P(19, 8)); p.drawLine(P(15, 20), P(19, 20))
    elif name == "addtext":
        p.drawRect(QRectF(3, 6, 18, 13))
        p.drawLine(P(7, 10), P(14, 10)); p.drawLine(P(10.5, 10), P(10.5, 16))
        p.drawLine(P(17, 1.5), P(17, 6.5)); p.drawLine(P(14.5, 4), P(19.5, 4))
    elif name == "erase":
        pen.setStyle(Qt.DashLine); pen.setDashPattern([2, 2]); p.setPen(pen)
        p.drawRect(QRectF(3, 5, 18, 14))
        pen.setStyle(Qt.SolidLine); p.setPen(pen)
        p.drawLine(P(8, 9), P(16, 15)); p.drawLine(P(16, 9), P(8, 15))
    elif name == "stamp":
        p.drawEllipse(QRectF(5, 3, 14, 10))
        p.drawLine(P(10, 13), P(9, 17)); p.drawLine(P(14, 13), P(15, 17))
        p.drawRoundedRect(QRectF(4, 17, 16, 3.5), 1, 1)
    elif name == "sign":
        path = QPainterPath(P(3, 16))
        path.cubicTo(6, 4, 9, 4, 8, 14); path.cubicTo(8, 20, 12, 8, 14, 12); path.cubicTo(15, 15, 17, 13, 21, 11)
        p.drawPath(path)
        p.drawLine(P(3, 20), P(21, 20))
    elif name in ("undo", "redo"):
        if name == "redo":
            p.translate(24, 0); p.scale(-1, 1)
        path = QPainterPath(P(8, 6)); path.lineTo(4, 10); path.lineTo(8, 14)
        p.drawPath(path)
        path = QPainterPath(P(4, 10)); path.lineTo(14, 10); path.cubicTo(22, 10, 22, 20, 14, 20); path.lineTo(10, 20)
        p.drawPath(path)
    elif name in ("zoomin", "zoomout"):
        p.drawEllipse(QRectF(3, 3, 13, 13)); p.drawLine(P(14, 14), P(21, 21))
        p.drawLine(P(6.5, 9.5), P(12.5, 9.5))
        if name == "zoomin":
            p.drawLine(P(9.5, 6.5), P(9.5, 12.5))
    elif name == "fitwidth":
        p.drawRect(QRectF(5, 3, 14, 18))
        p.drawLine(P(1, 12), P(23, 12)); p.drawLine(P(1, 12), P(4, 9)); p.drawLine(P(1, 12), P(4, 15))
        p.drawLine(P(23, 12), P(20, 9)); p.drawLine(P(23, 12), P(20, 15))
    elif name == "fitpage":
        p.drawRect(QRectF(6, 3, 12, 18))
        for a, b in ((P(3, 6), P(3, 2)), (P(21, 6), P(21, 2)), (P(3, 18), P(3, 22)), (P(21, 18), P(21, 22))):
            p.drawLine(a, b)
    elif name == "search":
        p.drawEllipse(QRectF(3, 3, 13, 13)); p.drawLine(P(14, 14), P(21, 21))
    elif name == "rotate":
        path = QPainterPath()
        path.arcMoveTo(QRectF(4, 4, 16, 16), 120); path.arcTo(QRectF(4, 4, 16, 16), 120, -270)
        p.drawPath(path)
        end = QPointF(12 + 8 * math.cos(math.radians(-150)), 12 - 8 * math.sin(math.radians(-150)))
        p.drawLine(end, end + P(4, 0)); p.drawLine(end, end + P(0, -4))
    elif name == "rotateleft":
        p.translate(24, 0); p.scale(-1, 1)
        _draw("rotate", p, c)
    elif name == "trash":
        p.drawLine(P(4, 6), P(20, 6)); p.drawRect(QRectF(9, 3, 6, 3))
        path = QPainterPath(P(6, 6)); path.lineTo(7, 21); path.lineTo(17, 21); path.lineTo(18, 6)
        p.drawPath(path)
        p.drawLine(P(10, 10), P(10, 17)); p.drawLine(P(14, 10), P(14, 17))
    elif name == "blank":
        p.drawRect(QRectF(5, 3, 14, 18)); p.drawLine(P(12, 8), P(12, 16)); p.drawLine(P(8, 12), P(16, 12))
    elif name == "insert":
        p.drawRect(QRectF(7, 3, 14, 18)); p.drawLine(P(1, 12), P(11, 12)); p.drawLine(P(8, 9), P(11, 12))
        p.drawLine(P(8, 15), P(11, 12))
    elif name == "extract":
        p.drawRect(QRectF(3, 3, 14, 18)); p.drawLine(P(11, 12), P(23, 12)); p.drawLine(P(20, 9), P(23, 12))
        p.drawLine(P(20, 15), P(23, 12))
    elif name == "split":
        p.drawRect(QRectF(3, 3, 8, 18)); p.drawRect(QRectF(13, 3, 8, 18))
    elif name == "merge":
        p.drawRect(QRectF(3, 3, 9, 12)); p.drawRect(QRectF(12, 9, 9, 12))
    elif name == "close":
        p.drawLine(P(6, 6), P(18, 18)); p.drawLine(P(18, 6), P(6, 18))
    elif name == "up":
        p.drawLine(P(6, 15), P(12, 9)); p.drawLine(P(12, 9), P(18, 15))
    elif name == "down":
        p.drawLine(P(6, 9), P(12, 15)); p.drawLine(P(12, 15), P(18, 9))
    elif name == "paragraph":
        for y in (6, 10, 14, 18):
            p.drawLine(P(4, y), P(20 if y < 18 else 13, y))
    else:
        p.drawRect(QRectF(4, 4, 16, 16))


def icon(name: str, white: bool = False) -> QIcon:
    key = (name, "#ffffff" if white else QGuiApplication.palette().color(QPalette.WindowText).name())
    if key in _cache:
        return _cache[key]
    ic = QIcon()
    for disabled in (False, True):
        for scale in (1, 2):
            pm = QPixmap(24 * scale, 24 * scale)
            pm.setDevicePixelRatio(scale)
            pm.fill(Qt.transparent)
            p = QPainter(pm)
            p.setRenderHint(QPainter.Antialiasing)
            _draw(name, p, QColor("#ffffff") if white else _ink(disabled))
            p.end()
            ic.addPixmap(pm, QIcon.Disabled if disabled else QIcon.Normal)
    _cache[key] = ic
    return ic


def clear_cache() -> None:
    _cache.clear()
