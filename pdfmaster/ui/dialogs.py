"""Диалоги: загрузка скана печати, рисование подписи, разделение документа."""
from __future__ import annotations


from PIL import Image
from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel,
                               QLineEdit, QPushButton, QRadioButton, QSlider, QSpinBox, QVBoxLayout, QWidget)

from ..core import images


def pil_to_qimage(img: Image.Image) -> QImage:
    img = img.convert("RGBA")
    data = img.tobytes("raw", "RGBA")
    return QImage(data, img.width, img.height, 4 * img.width, QImage.Format_RGBA8888).copy()


def checker(w: int, h: int, cell: int = 10) -> QPixmap:
    pm = QPixmap(w, h)
    pm.fill(QColor("#ffffff"))
    p = QPainter(pm)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#e6e6e6"))
    for y in range(0, h, cell):
        for x in range(0, w, cell):
            if (x // cell + y // cell) % 2:
                p.drawRect(x, y, cell, cell)
    p.end()
    return pm


class _Preview(QLabel):
    def __init__(self, title: str, transparent_bg: bool):
        super().__init__()
        self.title = title
        self.transparent_bg = transparent_bg
        self.setMinimumSize(300, 300)
        self.img: QImage | None = None

    def set_image(self, img: QImage | None) -> None:
        self.img = img
        self.update()

    def paintEvent(self, e) -> None:
        p = QPainter(self)
        r = self.rect().adjusted(4, 22, -4, -4)
        p.drawText(self.rect().adjusted(6, 2, 0, 0), Qt.AlignLeft | Qt.AlignTop, self.title)
        if self.transparent_bg:
            p.drawPixmap(r, checker(r.width(), r.height()))
        else:
            p.fillRect(r, QColor("#ffffff"))
        if self.img is not None and not self.img.isNull():
            k = min(r.width() / self.img.width(), r.height() / self.img.height())
            w, h = self.img.width() * k, self.img.height() * k
            tr = QRectF(r.x() + (r.width() - w) / 2, r.y() + (r.height() - h) / 2, w, h)
            p.setRenderHint(QPainter.SmoothPixmapTransform)
            p.drawImage(tr, self.img)
        p.setPen(QColor(0, 0, 0, 40))
        p.drawRect(r)
        p.end()


class StampImportDialog(QDialog):
    """Предпросмотр до/после удаления фона с ползунком порога."""

    def __init__(self, data: bytes, name: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Подпись или печать из скана")
        self.src = images.open_image(data)
        if max(self.src.size) > images.MAX_SIDE:
            k = images.MAX_SIDE / max(self.src.size)
            self.src = self.src.resize((round(self.src.width * k), round(self.src.height * k)), Image.LANCZOS)
        self.dpi = images.image_dpi(self.src)
        self.result: Image.Image | None = None
        lay = QVBoxLayout(self)
        row = QHBoxLayout()
        self.before = _Preview("До", False)
        self.after = _Preview("После (прозрачный фон)", True)
        row.addWidget(self.before, 1)
        row.addWidget(self.after, 1)
        lay.addLayout(row, 1)
        self.before.set_image(pil_to_qimage(self.src))
        ctl = QHBoxLayout()
        ctl.addWidget(QLabel("Порог фона:"))
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(3, 50)
        self.slider.setValue(16)
        self.slider.setToolTip("Левее — сохраняются бледные штрихи; правее — сильнее чистится фон")
        ctl.addWidget(self.slider, 1)
        self.val = QLabel("")
        ctl.addWidget(self.val)
        lay.addLayout(ctl)
        opt = QHBoxLayout()
        self.remove_bg = QCheckBox("Удалять фон")
        self.remove_bg.setChecked(True)
        opt.addWidget(self.remove_bg)
        opt.addWidget(QLabel("Тип:"))
        self.kind_stamp = QRadioButton("Печать")
        self.kind_sig = QRadioButton("Подпись")
        self.kind_stamp.setChecked(True)
        g = QButtonGroup(self)
        g.addButton(self.kind_stamp)
        g.addButton(self.kind_sig)
        opt.addWidget(self.kind_stamp)
        opt.addWidget(self.kind_sig)
        opt.addStretch(1)
        rl = QPushButton("↺ 90°")
        rl.setToolTip("Повернуть против часовой")
        rl.clicked.connect(lambda: self._rotate(90))
        rr = QPushButton("↻ 90°")
        rr.setToolTip("Повернуть по часовой")
        rr.clicked.connect(lambda: self._rotate(-90))
        opt.addWidget(QLabel("Повернуть:"))
        opt.addWidget(rl)
        opt.addWidget(rr)
        lay.addLayout(opt)
        self.info = QLabel("")
        self.info.setStyleSheet("color: palette(mid);")
        lay.addWidget(self.info)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Добавить в библиотеку")
        bb.button(QDialogButtonBox.Cancel).setText("Отмена")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self._timer = QTimer(self, singleShot=True, interval=120, timeout=self._update)
        self.slider.valueChanged.connect(lambda *_: self._timer.start())
        self.remove_bg.toggled.connect(lambda *_: self._timer.start())
        self.resize(820, 520)
        self._update()

    def _rotate(self, deg: int) -> None:
        self.src = self.src.rotate(deg, expand=True)
        self.before.set_image(pil_to_qimage(self.src))
        self._update()

    def _update(self) -> None:
        t = self.slider.value() / 100.0
        self.val.setText(f"{self.slider.value()}")
        if self.remove_bg.isChecked():
            self.result = images.remove_background(self.src, threshold=t)
        else:
            self.result = images.trim(self.src.convert("RGBA"), alpha_min=-1)
        self.after.set_image(pil_to_qimage(self.result))
        if self.dpi:
            mm = 25.4 / self.dpi
            self.info.setText(f"Размер: {self.result.width * mm:.0f} × {self.result.height * mm:.0f} мм "
                              f"(разрешение скана {self.dpi:.0f} dpi) — встанет в натуральную величину")
        else:
            self.info.setText(f"{self.result.width} × {self.result.height} пикселей. Разрешение скана "
                              "неизвестно — размер на листе можно поменять, потянув за угол.")

    def width_pt(self) -> float | None:
        if self.result is None or not self.dpi:
            return None
        return self.result.width / self.dpi * 72

    def kind(self) -> str:
        return "signature" if self.kind_sig.isChecked() else "stamp"


class SignaturePad(QWidget):
    def __init__(self):
        super().__init__()
        self.setMinimumSize(560, 220)
        self.setCursor(Qt.CrossCursor)
        self.strokes: list[list[QPointF]] = []
        self.color = QColor("#1a3fa3")
        self.pen_width = 2.6
        self.setAttribute(Qt.WA_StaticContents)

    def clear(self) -> None:
        self.strokes = []
        self.update()

    def mousePressEvent(self, e) -> None:
        self.strokes.append([e.position()])
        self.update()

    def mouseMoveEvent(self, e) -> None:
        if self.strokes and e.buttons() & Qt.LeftButton:
            self.strokes[-1].append(e.position())
            self.update()

    def tabletEvent(self, e) -> None:
        e.ignore()

    def _path(self, pts) -> QPainterPath:
        path = QPainterPath(pts[0])
        if len(pts) == 1:
            path.lineTo(pts[0] + QPointF(0.1, 0.1))
        for a, b in zip(pts, pts[1:]):
            mid = (a + b) / 2
            path.quadTo(a, mid)
        path.lineTo(pts[-1])
        return path

    def paint_to(self, p: QPainter) -> None:
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(self.color, self.pen_width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(Qt.NoBrush)
        for s in self.strokes:
            p.drawPath(self._path(s))

    def paintEvent(self, e) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#ffffff"))
        p.setPen(QPen(QColor(0, 0, 0, 60), 1, Qt.DashLine))
        p.drawRect(self.rect().adjusted(0, 0, -1, -1))
        p.drawLine(20, self.height() - 50, self.width() - 20, self.height() - 50)
        self.paint_to(p)
        p.end()

    def image(self, scale: int = 4) -> Image.Image | None:
        if not self.strokes:
            return None
        img = QImage(self.width() * scale, self.height() * scale, QImage.Format_RGBA8888)
        img.fill(Qt.transparent)
        p = QPainter(img)
        p.scale(scale, scale)
        self.paint_to(p)
        p.end()
        ptr = img.constBits()
        pil = Image.frombuffer("RGBA", (img.width(), img.height()), bytes(ptr), "raw", "RGBA", 0, 1)
        return images.trim(pil, pad=8)


class SignatureDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Нарисовать подпись")
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Распишитесь мышью или на трекпаде (с нажатием). Линию на образце не обязательно "
                             "соблюдать."))
        self.pad = SignaturePad()
        lay.addWidget(self.pad, 1)
        row = QHBoxLayout()
        row.addWidget(QLabel("Цвет:"))
        for name, c in (("синий", "#1a3fa3"), ("чёрный", "#111111")):
            b = QPushButton(f"● {name}")
            b.setStyleSheet(f"color: {c};")
            b.clicked.connect(lambda _=False, c=c: self._color(c))
            row.addWidget(b)
        row.addWidget(QLabel("Толщина:"))
        self.thick = QSlider(Qt.Horizontal)
        self.thick.setRange(10, 60)
        self.thick.setValue(26)
        self.thick.valueChanged.connect(lambda v: self._thick(v))
        row.addWidget(self.thick)
        row.addStretch(1)
        clr = QPushButton("Очистить")
        clr.clicked.connect(self.pad.clear)
        row.addWidget(clr)
        lay.addLayout(row)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Добавить в библиотеку")
        bb.button(QDialogButtonBox.Cancel).setText("Отмена")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.resize(640, 340)

    def _color(self, c: str) -> None:
        self.pad.color = QColor(c)
        self.pad.update()

    def _thick(self, v: int) -> None:
        self.pad.pen_width = v / 10.0
        self.pad.update()


class SplitDialog(QDialog):
    def __init__(self, count: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Разделить документ")
        self.count = count
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(f"В документе листов: {count}. Каждая часть сохранится отдельным файлом."))
        self.by_ranges = QRadioButton("По диапазонам:")
        self.ranges = QLineEdit()
        self.ranges.setPlaceholderText("например: 1-3, 4-7, 8-конец")
        self.by_n = QRadioButton("Каждые N листов:")
        self.n = QSpinBox()
        self.n.setRange(1, max(1, count))
        self.n.setValue(1)
        self.by_ranges.setChecked(True)
        r1 = QHBoxLayout()
        r1.addWidget(self.by_ranges)
        r1.addWidget(self.ranges, 1)
        r2 = QHBoxLayout()
        r2.addWidget(self.by_n)
        r2.addWidget(self.n)
        r2.addStretch(1)
        lay.addLayout(r1)
        lay.addLayout(r2)
        self.err = QLabel("")
        self.err.setStyleSheet("color: #c0262d;")
        lay.addWidget(self.err)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Разделить…")
        bb.button(QDialogButtonBox.Cancel).setText("Отмена")
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.parts: list[list[int]] = []

    def _ok(self) -> None:
        from ..core import ops
        try:
            if self.by_ranges.isChecked():
                self.parts = ops.parse_ranges(self.ranges.text(), self.count)
            else:
                self.parts = ops.every_n(self.count, self.n.value())
        except ValueError as e:
            self.err.setText(str(e))
            return
        self.accept()
