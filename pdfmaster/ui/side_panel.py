"""Правая панель: свойства текста, библиотека подписей и печатей, листы."""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
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


def _line() -> QFrame:
    f = QFrame()
    f.setFrameShape(QFrame.HLine)
    f.setStyleSheet("color: palette(midlight);")
    return f


class ColorButton(QToolButton):
    def __init__(self, color: str, parent=None):
        super().__init__(parent)
        self.color = color
        self.setFixedSize(22, 22)
        self.setCursor(Qt.PointingHandCursor)
        self._paint()

    def set_color(self, c: str) -> None:
        self.color = c
        self._paint()

    def _paint(self) -> None:
        pm = QPixmap(36, 36)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.setBrush(QColor(self.color))
        p.setPen(QColor(255, 255, 255, 200))
        p.drawEllipse(3, 3, 30, 30)
        p.end()
        self.setIcon(QIcon(pm))
        self.setIconSize(QSize(18, 18))


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
        self.size = QDoubleSpinBox()
        self.size.setRange(3, 300)
        self.size.setDecimals(1)
        self.size.setSingleStep(0.5)
        self.size.setSuffix(" пт")
        self.size.setKeyboardTracking(False)
        g.addWidget(self.size, 1, 1)
        self.bold = QToolButton()
        self.bold.setText("Ж")
        self.bold.setCheckable(True)
        self.bold.setStyleSheet("font-weight: bold; min-width: 26px;")
        self.bold.setToolTip("Жирный")
        self.italic = QToolButton()
        self.italic.setText("К")
        self.italic.setCheckable(True)
        self.italic.setStyleSheet("font-style: italic; min-width: 26px;")
        self.italic.setToolTip("Курсив")
        g.addWidget(self.bold, 1, 2)
        g.addWidget(self.italic, 1, 3)
        g.addWidget(QLabel("Цвет"), 2, 0)
        row = QHBoxLayout()
        self.color_btns = []
        for c in PRESET_COLORS:
            b = ColorButton(c)
            b.clicked.connect(lambda _=False, c=c: self._set_color(c))
            row.addWidget(b)
            self.color_btns.append(b)
        self.more = QToolButton()
        self.more.setText("…")
        self.more.setToolTip("Другой цвет")
        self.more.clicked.connect(self._pick_color)
        row.addWidget(self.more)
        row.addStretch(1)
        g.addLayout(row, 2, 1, 1, 3)
        lay.addLayout(g)
        self.cur_color = ColorButton("#000000")
        self.cur_color.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.cur_color.setToolTip("Текущий цвет")
        row.insertWidget(0, self.cur_color)
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
            self.family.addItem(f"Как в документе ({F.clean_font_name(doc_font)[:28]})", "orig")
        for k in F.FAMILY_ORDER:
            self.family.addItem(F.FAMILIES[k][0], k)
        i = self.family.findData(family)
        self.family.setCurrentIndex(max(0, i))
        self.size.setValue(size)
        self.bold.setChecked(bold)
        self.italic.setChecked(italic)
        self.cur_color.set_color(color)
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

    def _set_color(self, c: str) -> None:
        self.cur_color.set_color(c)
        if not self._loading:
            self.changed.emit({"color": c})

    def _pick_color(self) -> None:
        c = QColorDialog.getColor(QColor(self.cur_color.color), self, "Цвет текста")
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
        self.lib = QListWidget()
        self.lib.setViewMode(QListWidget.IconMode)
        self.lib.setIconSize(QSize(96, 72))
        self.lib.setGridSize(QSize(112, 96))
        self.lib.setResizeMode(QListWidget.Adjust)
        self.lib.setMovement(QListWidget.Static)
        self.lib.setMinimumHeight(220)
        self.lib.setSpacing(4)
        self.lib.setStyleSheet("QListWidget::item:selected { background: rgba(214,69,47,0.18); border-radius: 6px; }")
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
            li = QListWidgetItem(icon_for(it), "")
            li.setData(Qt.UserRole, it["id"])
            li.setToolTip(it.get("name", ""))
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
        self.white = QCheckBox("Закрасить область белым")
        self.white.setToolTip("Скрывает и то, что попало в рамку частично (например, длинные линии)")
        self.white.toggled.connect(self.whiteChanged.emit)
        lay.addWidget(self.white)
        self.del_btn = QPushButton(icons.icon("trash"), "Отменить удаление этой области")
        self.del_btn.clicked.connect(self.deleteObj.emit)
        lay.addWidget(self.del_btn)
        lay.addStretch(1)

    def show_obj(self, o: dict | None) -> None:
        self.white.blockSignals(True)
        self.white.setChecked(bool(o.get("white", True)) if o else True)
        self.white.setEnabled(o is not None)
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
