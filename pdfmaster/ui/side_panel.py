"""Правая панель: свойства текста, библиотека подписей и печатей, листы."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QCheckBox, QColorDialog, QComboBox, QDoubleSpinBox, QFrame, QGridLayout,
                               QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton, QScrollArea,
                               QSizePolicy, QStackedWidget, QToolButton, QVBoxLayout, QWidget)

from ..core import fonts as F
from . import icons

PRESET_COLORS = ["#000000", "#1a3fa3", "#c0262d", "#1d7a35"]


def _title(text: str) -> QLabel:
    """Заголовок раздела панели (стиль — в ui/theme.py, role=title)."""
    lb = QLabel(text)
    lb.setProperty("role", "title")
    return lb


def _hint(text: str) -> QLabel:
    lb = QLabel(text)
    lb.setWordWrap(True)
    lb.setProperty("role", "hint")
    return lb


def _section(text: str) -> QLabel:
    lb = QLabel(text)
    lb.setWordWrap(True)
    lb.setProperty("role", "section")
    return lb


def _line() -> QFrame:
    f = QFrame()
    f.setFrameShape(QFrame.HLine)
    f.setStyleSheet("color: palette(midlight);")
    return f


class ColorButton(QToolButton):
    """Кружок цвета; выбранный обведён кольцом фирменного цвета."""

    def __init__(self, color: str, parent=None):
        super().__init__(parent)
        self.color = color
        self.selected = False
        self.setFixedSize(28, 28)
        self.setAutoRaise(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet("QToolButton { border: none; background: transparent; }")
        self._paint()

    def set_color(self, c: str) -> None:
        self.color = c
        self._paint()

    def set_selected(self, on: bool) -> None:
        self.selected = on
        self._paint()

    def _paint(self) -> None:
        from .theme import color as tcolor
        pm = QPixmap(56, 56)
        pm.setDevicePixelRatio(2)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        if self.selected:
            p.setPen(QPen(tcolor("accent"), 2.0))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(QPointF(14, 14), 12, 12)
        p.setPen(QPen(QColor(0, 0, 0, 40), 1))
        p.setBrush(QColor(self.color))
        p.drawEllipse(QPointF(14, 14), 8.5, 8.5)
        p.end()
        self.setIcon(QIcon(pm))
        self.setIconSize(QSize(28, 28))


class TextProps(QWidget):
    """Свойства правки текста/нового текста."""
    changed = Signal(dict)
    paragraph = Signal()
    revert = Signal()

    def __init__(self):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.title = _title("Текст")
        lay.addWidget(self.title)
        self.hint = _hint("")
        lay.addWidget(self.hint)
        g = QGridLayout()
        g.addWidget(QLabel("Шрифт"), 0, 0)
        self.family = QComboBox()
        g.addWidget(self.family, 0, 1, 1, 3)
        g.addWidget(QLabel("Размер"), 1, 0)
        srow = QHBoxLayout()
        srow.setSpacing(6)
        self.size = QDoubleSpinBox()
        self.size.setRange(3, 300)
        self.size.setDecimals(1)
        self.size.setSingleStep(0.5)
        self.size.setSuffix(" пт")
        self.size.setKeyboardTracking(False)
        self.size.setMinimumWidth(92)
        srow.addWidget(self.size)
        srow.addSpacing(6)
        self.bold = QToolButton()
        self.bold.setText("Ж")
        self.bold.setCheckable(True)
        self.bold.setProperty("toggle", True)
        f = self.bold.font()
        f.setBold(True)
        self.bold.setFont(f)
        self.bold.setToolTip("Жирный")
        self.italic = QToolButton()
        self.italic.setText("К")
        self.italic.setCheckable(True)
        self.italic.setProperty("toggle", True)
        f = self.italic.font()
        f.setItalic(True)
        self.italic.setFont(f)
        self.italic.setToolTip("Курсив")
        srow.addWidget(self.bold)
        srow.addWidget(self.italic)
        srow.addStretch(1)
        g.addLayout(srow, 1, 1, 1, 3)
        g.addWidget(QLabel("Цвет"), 2, 0)
        row = QHBoxLayout()
        row.setSpacing(2)
        self.color_btns = []
        for c in PRESET_COLORS:
            b = ColorButton(c)
            b.setToolTip({"#000000": "Чёрный", "#1a3fa3": "Синий", "#c0262d": "Красный",
                          "#1d7a35": "Зелёный"}.get(c, c))
            b.clicked.connect(lambda _=False, c=c: self._set_color(c))
            row.addWidget(b)
            self.color_btns.append(b)
        self.custom_btn = ColorButton("#888888")
        self.custom_btn.setToolTip("Другой цвет…")
        self.custom_btn.setVisible(False)
        self.custom_btn.clicked.connect(self._pick_color)
        row.addWidget(self.custom_btn)
        self.more = QToolButton()
        self.more.setText("Другой…")
        self.more.setProperty("chip", True)
        self.more.setCursor(Qt.PointingHandCursor)
        self.more.setToolTip("Выбрать любой цвет")
        self.more.clicked.connect(self._pick_color)
        row.addWidget(self.more)
        row.addStretch(1)
        g.addLayout(row, 2, 1, 1, 3)
        g.setColumnStretch(1, 1)
        g.setHorizontalSpacing(10)
        g.setVerticalSpacing(10)
        lay.addLayout(g)
        self._color = "#000000"
        self.fit = QCheckBox("Уместить в прежние строки (уменьшить шрифт)")
        self.fit.setToolTip("Если новый текст длиннее, кегль уменьшается (до 70 %), чтобы абзац не наползал на "
                            "следующий текст")
        self.fit.toggled.connect(lambda v: None if self._loading else self.changed.emit({"fit": v}))
        lay.addWidget(self.fit)
        self.para_btn = QPushButton(icons.icon("paragraph"), "Править абзац целиком")
        self.para_btn.setToolTip("Текст будет заново перенесён по ширине абзаца")
        self.para_btn.clicked.connect(self.paragraph.emit)
        lay.addWidget(self.para_btn)
        self.revert_btn = QPushButton("Вернуть исходный текст")
        self.revert_btn.clicked.connect(self.revert.emit)
        lay.addWidget(self.revert_btn)
        self.keys = _hint("Enter — готово, Esc — готово.\nВ абзаце: Enter — новая строка, ⌘Enter — готово.")
        lay.addWidget(self.keys)
        lay.addStretch(1)
        self._loading = False
        self.family.currentIndexChanged.connect(lambda *_: self._emit("family"))
        self.size.valueChanged.connect(lambda *_: self._emit("size"))
        self.bold.toggled.connect(lambda *_: self._emit("bold"))
        self.italic.toggled.connect(lambda *_: self._emit("italic"))

    def load(self, *, kind: str, family: str, size: float, bold: bool, italic: bool, color: str,
             doc_font: str = "", hint: str = "", fit: bool = False) -> None:
        self._loading = True
        self.fit.setVisible(kind == "para")
        self.fit.setChecked(fit)
        self.family.clear()
        if kind != "text":
            self.family.addItem("Как в документе", "orig")
            self.family.setItemData(0, f"Шрифт документа: {F.clean_font_name(doc_font)}", Qt.ToolTipRole)
            self.family.setToolTip(f"Шрифт документа: {F.clean_font_name(doc_font)}")
        for k in F.FAMILY_ORDER:
            self.family.addItem(F.FAMILIES[k][0], k)
        i = self.family.findData(family)
        self.family.setCurrentIndex(max(0, i))
        self.size.setValue(size)
        self.bold.setChecked(bold)
        self.italic.setChecked(italic)
        self._show_color(color)
        self.title.setText({"line": "Правка строки", "para": "Правка абзаца", "text": "Новый текст"}.get(kind, "Текст"))
        self.para_btn.setVisible(kind == "line")
        self.revert_btn.setVisible(kind in ("line", "para"))
        self.keys.setText("Enter или Esc — готово." if kind == "line" else
                          "Enter — новая строка; ⌘Enter, Esc или клик мимо — готово.")
        self.hint.setText(hint)
        self.hint.setVisible(bool(hint))
        self._loading = False

    def set_hint(self, text: str, warn: bool = False) -> None:
        self.hint.setText(text)
        self.hint.setStyleSheet("color: #c0262d; font-size: 11px;" if warn else "")
        self.hint.setVisible(bool(text))

    def _show_color(self, c: str) -> None:
        self._color = c.lower()
        known = False
        for b in self.color_btns:
            on = b.color.lower() == self._color
            known = known or on
            b.set_selected(on)
        self.custom_btn.setVisible(not known)
        if not known:
            self.custom_btn.set_color(c)
            self.custom_btn.set_selected(True)

    def _set_color(self, c: str) -> None:
        self._show_color(c)
        if not self._loading:
            self.changed.emit({"color": c})

    def _pick_color(self) -> None:
        c = QColorDialog.getColor(QColor(self._color), self, "Цвет текста")
        if c.isValid():
            self._set_color(c.name())

    def _emit(self, what: str) -> None:
        if self._loading:
            return
        if what == "family":
            self.changed.emit({"family": self.family.currentData()})
        elif what == "size":
            self.changed.emit({"size": round(self.size.value(), 2)})
        elif what == "bold":
            self.changed.emit({"bold": self.bold.isChecked()})
        elif what == "italic":
            self.changed.emit({"italic": self.italic.isChecked()})


class StampPanel(QWidget):
    """Библиотека подписей и печатей + действия с выбранной на листе."""
    importScan = Signal()
    drawSignature = Signal()
    picked = Signal(object)          # id элемента библиотеки или None
    removeItem = Signal(str)
    renameItem = Signal(str)
    toPages = Signal(str)            # "all" | "selected"
    rotate = Signal(int)
    deleteObj = Signal()

    def __init__(self):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(_title("Подписи и печати"))
        lay.addWidget(_hint("Выберите образец и кликните на лист. Потом его можно двигать, тянуть за угол "
                            "(пропорции сохраняются) и вращать за верхний кружок."))
        b1 = QPushButton(icons.icon("stamp"), "Загрузить скан…")
        b1.setToolTip("JPG, PNG, HEIC или PDF; фон удаляется автоматически")
        b1.clicked.connect(self.importScan.emit)
        b2 = QPushButton(icons.icon("sign"), "Нарисовать подпись…")
        b2.clicked.connect(self.drawSignature.emit)
        lay.addWidget(b1)
        lay.addWidget(b2)
        lay.addSpacing(6)
        lay.addWidget(_section("БИБЛИОТЕКА — кликните образец, затем место на листе"))
        self.lib = QListWidget()
        self.lib.setObjectName("stampLib")
        self.lib.setViewMode(QListWidget.IconMode)
        self.lib.setIconSize(QSize(104, 70))
        self.lib.setGridSize(QSize(124, 132))
        self.lib.setResizeMode(QListWidget.Adjust)
        self.lib.setMovement(QListWidget.Static)
        self.lib.setWordWrap(True)
        self.lib.setUniformItemSizes(True)
        self.lib.setMinimumHeight(250)
        self.lib.setSpacing(2)
        self.lib.setCursor(Qt.PointingHandCursor)
        self.lib.itemSelectionChanged.connect(self._sel)
        self.lib.setContextMenuPolicy(Qt.CustomContextMenu)
        self.lib.customContextMenuRequested.connect(self._menu)
        lay.addWidget(self.lib, 1)
        self.empty = _hint("Пока пусто. Загрузите скан подписи или печати (лучше на светлом фоне) "
                           "или нарисуйте подпись.")
        lay.addWidget(self.empty)
        lay.addWidget(_line())
        self.obj_box = QWidget()
        ol = QVBoxLayout(self.obj_box)
        ol.setContentsMargins(0, 0, 0, 0)
        ol.addWidget(_title("Выбранная на листе"))
        self.size_lbl = _hint("")
        ol.addWidget(self.size_lbl)
        a = QPushButton("Поставить на все листы")
        a.clicked.connect(lambda: self.toPages.emit("all"))
        s = QPushButton("Поставить на выбранные листы")
        s.setToolTip("Выберите листы в колонке миниатюр (⌘-клик)")
        s.clicked.connect(lambda: self.toPages.emit("selected"))
        ol.addWidget(a)
        ol.addWidget(s)
        r2 = QHBoxLayout()
        rl = QPushButton(icons.icon("rotateleft"), "")
        rl.setToolTip("Повернуть против часовой на 90°")
        rl.clicked.connect(lambda: self.rotate.emit(-90))
        rr = QPushButton(icons.icon("rotate"), "")
        rr.setToolTip("Повернуть по часовой на 90°")
        rr.clicked.connect(lambda: self.rotate.emit(90))
        dl = QPushButton(icons.icon("trash"), "Удалить")
        dl.clicked.connect(self.deleteObj.emit)
        r2.addWidget(rl)
        r2.addWidget(rr)
        r2.addWidget(dl)
        ol.addLayout(r2)
        lay.addWidget(self.obj_box)
        self.obj_box.setVisible(False)
        self._items: list[dict] = []

    def set_items(self, items: list[dict], icon_for, picked: str | None) -> None:
        self.lib.blockSignals(True)
        self.lib.clear()
        self._items = items
        for it in items:
            name = it.get("name") or ("Подпись" if it.get("kind") == "signature" else "Печать")
            short = name if len(name) <= 16 else name[:15] + "…"
            li = QListWidgetItem(icon_for(it), short)
            li.setData(Qt.UserRole, it["id"])
            li.setToolTip(name + "\nПравый клик — переименовать или убрать")
            li.setTextAlignment(Qt.AlignHCenter | Qt.AlignTop)
            self.lib.addItem(li)
            if it["id"] == picked:
                li.setSelected(True)
        self.lib.blockSignals(False)
        self.empty.setVisible(not items)

    def _sel(self) -> None:
        its = self.lib.selectedItems()
        self.picked.emit(its[0].data(Qt.UserRole) if its else None)

    def _menu(self, pos) -> None:
        from PySide6.QtWidgets import QMenu
        it = self.lib.itemAt(pos)
        if it is None:
            return
        m = QMenu(self)
        m.addAction("Переименовать…", lambda: self.renameItem.emit(it.data(Qt.UserRole)))
        m.addAction("Убрать из библиотеки", lambda: self.removeItem.emit(it.data(Qt.UserRole)))
        m.exec(self.lib.mapToGlobal(pos))

    def show_obj(self, o: dict | None) -> None:
        self.obj_box.setVisible(o is not None)
        if o is not None:
            mm = 25.4 / 72
            self.size_lbl.setText(f"Размер: {o['w'] * mm:.0f} × {o['h'] * mm:.0f} мм, поворот {o.get('angle', 0) % 360:.0f}°")


class PagesPanel(QWidget):
    action = Signal(str)

    def __init__(self):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(_title("Листы"))
        self.info = _hint("")
        lay.addWidget(self.info)
        for key, ic, text, tip in (
                ("rot", "rotate", "Повернуть по часовой", "Повернуть выбранные листы (или текущий)"),
                ("rotl", "rotateleft", "Повернуть против часовой", ""),
                ("blank", "blank", "Вставить пустой лист", "После текущего листа"),
                ("insert", "insert", "Вставить PDF или картинку…", "После текущего листа"),
                ("extract", "extract", "Извлечь в отдельный PDF…", "Выбранные листы"),
                ("split", "split", "Разделить документ…", "По диапазонам или каждые N листов"),
                ("delete", "trash", "Удалить листы", "Выбранные листы (или текущий)")):
            b = QPushButton(icons.icon(ic), "  " + text)
            b.setToolTip(tip)
            b.setProperty("listitem", True)
            if key == "delete":
                b.setProperty("danger", True)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, k=key: self.action.emit(k))
            lay.addWidget(b)
        lay.addSpacing(8)
        lay.addWidget(_hint("Порядок листов меняется перетаскиванием миниатюр. ⌘-клик и ⇧-клик — выбрать "
                            "несколько. Файлы из Finder можно бросить прямо в колонку миниатюр."))
        lay.addStretch(1)

    def set_info(self, n_sel: int, cur: int, total: int) -> None:
        self.info.setText(f"Выбрано листов: {n_sel}" if n_sel else f"Текущий лист: {cur + 1} из {total}")


class EraseProps(QWidget):
    whiteChanged = Signal(bool)
    deleteObj = Signal()

    def __init__(self):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(_title("Удалить область"))
        lay.addWidget(_hint("Обведите рамкой область листа: текст, картинки и линии, целиком попавшие в неё, "
                            "будут удалены из файла по-настоящему."))
        self.white = QCheckBox("Закрашивать область белым")
        self.white.setChecked(True)
        self.white.setToolTip("Скрывает и то, что попало в рамку лишь частично (например, длинные линии). "
                              "Без выделенной области — настройка для новых рамок.")
        self.white.toggled.connect(self.whiteChanged.emit)
        lay.addWidget(self.white)
        self.del_btn = QPushButton(icons.icon("trash"), "Отменить удаление этой области")
        self.del_btn.clicked.connect(self.deleteObj.emit)
        lay.addWidget(self.del_btn)
        lay.addStretch(1)

    def show_obj(self, o: dict | None, default_white: bool = True) -> None:
        self.white.blockSignals(True)
        self.white.setChecked(bool(o.get("white", True)) if o else default_white)
        self.white.blockSignals(False)
        self.del_btn.setVisible(o is not None)


class SidePanel(QScrollArea):
    def __init__(self):
        super().__init__()
        self.setObjectName("sidePanel")
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setFrameShape(QFrame.NoFrame)
        self.setMinimumWidth(300)
        self.setMaximumWidth(400)
        inner = QWidget()
        inner.setObjectName("propsPanel")
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(16, 16, 16, 16)
        self.stack = QStackedWidget()
        self.text = TextProps()
        self.stamps = StampPanel()
        self.pages = PagesPanel()
        self.erase = EraseProps()
        from .measure_tool import MeasurePanel
        self.measure = MeasurePanel()
        for w in (self.pages, self.text, self.stamps, self.erase, self.measure):
            self.stack.addWidget(w)
        lay.addWidget(self.stack)
        lay.addStretch(0)
        self.setWidget(inner)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)

    def resizeEvent(self, e) -> None:
        # содержимое строго по ширине видимой области — ничего не уезжает за край
        super().resizeEvent(e)
        self.widget().setFixedWidth(self.viewport().width())

    def show_page(self, name: str) -> None:
        self.stack.setCurrentWidget({"text": self.text, "stamps": self.stamps, "pages": self.pages,
                                     "erase": self.erase, "measure": self.measure}[name])
