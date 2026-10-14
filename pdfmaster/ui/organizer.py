"""Режим «Систематизировать листы» — крупная сетка миниатюр, как в Acrobat."""
from __future__ import annotations

from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QMenu, QPushButton, QSlider, QToolButton,
                               QVBoxLayout, QWidget)

from . import icons
from .thumbs import ThumbPanel


class Organizer(QWidget):
    def __init__(self, tab):
        super().__init__()
        self.tab = tab
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        bar = QFrame()
        bar.setObjectName("organizerBar")
        bar.setStyleSheet("#organizerBar { border-bottom: 1px solid palette(midlight); }")
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(12, 6, 12, 6)
        title = QLabel("Систематизировать листы")
        f = title.font()
        f.setBold(True)
        f.setPointSizeF(f.pointSizeF() + 1)
        title.setFont(f)
        bl.addWidget(title)
        bl.addSpacing(12)

        def btn(text, ic, slot, tip=""):
            b = QToolButton()
            b.setText(text)
            b.setIcon(icons.icon(ic))
            b.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
            b.setAutoRaise(True)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            bl.addWidget(b)
            return b

        btn("", "rotateleft", lambda: self._act("rotl"), "Повернуть против часовой")
        btn("", "rotate", lambda: self._act("rot"), "Повернуть по часовой")
        btn("Удалить", "trash", lambda: self._act("delete"), "Удалить выбранные листы (⌫)")
        ins = btn("Вставить", "insert", self._insert_menu, "Вставить пустой лист или файл после выбранного")
        ins.setPopupMode(QToolButton.InstantPopup)
        btn("Извлечь…", "extract", lambda: self._act("extract"), "Выбранные листы — в отдельный PDF")
        btn("Разделить…", "split", tab.split_doc, "По диапазонам или каждые N листов")
        self.info = QLabel("")
        self.info.setStyleSheet("color: palette(mid); margin-left: 10px;")
        bl.addWidget(self.info)
        bl.addStretch(1)
        bl.addWidget(QLabel("Размер:"))
        self.size = QSlider(Qt.Horizontal)
        self.size.setRange(100, 340)
        self.size.setValue(170)
        self.size.setFixedWidth(140)
        bl.addWidget(self.size)
        done = QPushButton("Готово")
        done.setDefault(True)
        done.clicked.connect(lambda: tab.ctx.request_organizer(False))
        bl.addWidget(done)
        lay.addWidget(bar)
        self.grid = ThumbPanel(tab, grid=True)
        lay.addWidget(self.grid, 1)
        hint = QLabel("Перетаскивайте листы, чтобы поменять порядок; ⌘/⇧-клик — несколько; «+» между листами — "
                      "вставить; файлы из Finder можно бросить прямо сюда; двойной клик — открыть лист.")
        hint.setStyleSheet("color: palette(mid); padding: 6px 12px;")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        self.size.valueChanged.connect(self.grid.set_thumb_width)
        g = self.grid
        g.orderChanged.connect(tab._on_thumb_order)
        g.filesDropped.connect(tab.insert_files)
        g.contextAction.connect(tab._thumb_action)
        g.selectionPids.connect(self._sel_changed)
        g.insertAt.connect(self._insert_at)
        g.pageOpened.connect(self._open_page)

    def _pids(self) -> list:
        return self.grid.selected_pids() or ([self.tab.model.pages[self.tab.view.current_page()]["id"]]
                                             if self.tab.model.pages else [])

    def _act(self, name: str) -> None:
        pids = self._pids()
        if pids:
            self.tab._thumb_action(name, pids)

    def _sel_changed(self, pids) -> None:
        n = len(pids)
        self.info.setText(f"Выбрано листов: {n} из {len(self.tab.model.pages)}" if n
                          else f"Листов: {len(self.tab.model.pages)}")

    def _insert_menu(self) -> None:
        pids = self._pids()
        at = (self.tab.model.page_index(pids[-1]) + 1) if pids else len(self.tab.model.pages)
        self._insert_at(at, self.mapToGlobal(QPoint(300, 40)))

    def _insert_at(self, at: int, gpos: QPoint) -> None:
        m = QMenu(self)
        m.addAction(icons.icon("blank"), "Пустой лист", lambda: self.tab.insert_blank(at))
        m.addAction(icons.icon("insert"), "Из файла (PDF, JPG, PNG, HEIC)…", lambda: self.tab.insert_files_dialog(at))
        m.exec(gpos)

    def _open_page(self, i: int) -> None:
        self.tab.ctx.request_organizer(False)
        self.tab.view.go_to_page(i)

    def rebuild(self) -> None:
        self.grid.rebuild()
        self._sel_changed(self.grid.selected_pids())
