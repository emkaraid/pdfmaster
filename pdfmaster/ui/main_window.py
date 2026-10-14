"""Главное окно: меню, панель инструментов, вкладки документов."""
from __future__ import annotations

import os

from PySide6.QtCore import QObject, QSettings, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QActionGroup, QGuiApplication, QKeySequence, QPalette
from PySide6.QtWidgets import (QFileDialog, QHBoxLayout, QInputDialog, QLabel, QMainWindow,
                               QMessageBox, QPushButton, QSizePolicy, QSpinBox, QStackedWidget, QTabWidget,
                               QToolBar, QToolButton, QVBoxLayout, QWidget)

from ..core.library import Library
from ..core.model import DocModel
from ..core.saver import COMPRESS
from ..render.service import RenderService
from . import icons
from .doc_tab import OPEN_FILTER, DocTab, open_model

APP_TITLE = "PDF‑мастерская"


class AppContext(QObject):
    libraryChanged = Signal()
    modeRequested = Signal(str)
    organizerRequested = Signal(bool)

    def __init__(self):
        super().__init__()
        self.settings = QSettings("PDFMaster", "PDF-masterskaya")
        self.service = RenderService()
        self.library = Library()
        pal = QGuiApplication.palette()
        self.dark = pal.color(QPalette.Window).lightness() < 128

    def last_dir(self) -> str:
        d = self.settings.value("last_dir", os.path.expanduser("~/Documents"))
        return d if d and os.path.isdir(d) else os.path.expanduser("~")

    def set_last_dir(self, path: str) -> None:
        self.settings.setValue("last_dir", path if os.path.isdir(path) else os.path.dirname(path))

    def compress_mode(self) -> str:
        v = self.settings.value("compress", "none")
        return v if v in COMPRESS else "none"

    def request_mode(self, mode: str) -> None:
        self.modeRequested.emit(mode)

    def request_organizer(self, on: bool) -> None:
        self.organizerRequested.emit(on)

    def recent(self) -> list[str]:
        v = self.settings.value("recent", [])
        if isinstance(v, str):
            v = [v]
        return [p for p in (v or []) if os.path.exists(p)]

    def add_recent(self, path: str) -> None:
        r = [p for p in self.recent() if p != path]
        r.insert(0, path)
        self.settings.setValue("recent", r[:12])


class Toast(QLabel):
    def __init__(self, parent):
        super().__init__(parent)
        self.setStyleSheet("QLabel { background: rgba(29,35,48,0.92); color: white; padding: 9px 16px;"
                           " border-radius: 9px; font-size: 13px; }")
        self.setWordWrap(True)
        self.hide()
        self._t = QTimer(self, singleShot=True, timeout=self.hide)

    def show_msg(self, msg: str, ms: int = 3200) -> None:
        self.setText(msg)
        self.setMaximumWidth(int(self.parent().width() * 0.7))
        self.adjustSize()
        p = self.parent()
        self.move((p.width() - self.width()) // 2, p.height() - self.height() - 42)
        self.raise_()
        self.show()
        self._t.start(ms)


class EmptyState(QWidget):
    def __init__(self, win: "MainWindow"):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.addStretch(1)
        t = QLabel(APP_TITLE)
        f = t.font()
        f.setPointSize(24)
        f.setBold(True)
        t.setFont(f)
        t.setAlignment(Qt.AlignCenter)
        s = QLabel("Перетащите сюда PDF (или несколько — откроются во вкладках)\nили нажмите «Открыть».\n"
                   "Файлы никуда не отправляются: всё работает на этом Mac, без интернета.")
        s.setAlignment(Qt.AlignCenter)
        s.setStyleSheet("color: palette(mid);")
        b = QPushButton(icons.icon("open"), "Открыть PDF…")
        b.setMinimumWidth(180)
        b.clicked.connect(win.open_dialog)
        m = QPushButton(icons.icon("merge"), "Объединить файлы…")
        m.setMinimumWidth(180)
        m.clicked.connect(win.merge_dialog)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(b)
        row.addWidget(m)
        row.addStretch(1)
        lay.addWidget(t)
        lay.addSpacing(8)
        lay.addWidget(s)
        lay.addSpacing(16)
        lay.addLayout(row)
        self.recent_box = QVBoxLayout()
        lay.addSpacing(18)
        lay.addLayout(self.recent_box)
        lay.addStretch(2)
        self.win = win

    def refresh(self) -> None:
        while self.recent_box.count():
            it = self.recent_box.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        rec = self.win.ctx.recent()[:6]
        if rec:
            lb = QLabel("Недавние:")
            lb.setAlignment(Qt.AlignCenter)
            lb.setStyleSheet("color: palette(mid);")
            self.recent_box.addWidget(lb)
        for p in rec:
            b = QPushButton(os.path.basename(p))
            b.setFlat(True)
            b.setToolTip(p)
            b.setStyleSheet("color: palette(link);")
            b.clicked.connect(lambda _=False, p=p: self.win.open_paths([p]))
            self.recent_box.addWidget(b, 0, Qt.AlignCenter)


class MainWindow(QMainWindow):
    def __init__(self, ctx: AppContext):
        super().__init__()
        self.ctx = ctx
        self.setWindowTitle(APP_TITLE)
        self.setUnifiedTitleAndToolBarOnMac(True)
        self.setAcceptDrops(True)
        self.resize(1400, 900)
        g = ctx.settings.value("geometry")
        if g is not None:
            self.restoreGeometry(g)
        self.stack = QStackedWidget()
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.setTabsClosable(True)
        self.tabs.setMovable(True)
        self.tabs.setElideMode(Qt.ElideMiddle)
        self.tabs.tabCloseRequested.connect(self.close_tab)
        self.tabs.currentChanged.connect(self._tab_changed)
        self.empty = EmptyState(self)
        self.stack.addWidget(self.empty)
        self.stack.addWidget(self.tabs)
        self.setCentralWidget(self.stack)
        self.toast = Toast(self)
        self._build_actions()
        self._build_menus()
        self._build_toolbar()
        self._build_status()
        ctx.modeRequested.connect(self.set_mode)
        ctx.organizerRequested.connect(self.show_organizer)
        self.empty.refresh()
        self._update_actions()

    # ------------------------------------------------------------ действия

    def _act(self, text, slot, shortcut=None, icon=None, tip=None, checkable=False) -> QAction:
        a = QAction(text, self)
        if icon:
            a.setIcon(icons.icon(icon))
        if shortcut:
            a.setShortcuts([QKeySequence(s) for s in shortcut] if isinstance(shortcut, (list, tuple))
                           else [QKeySequence(shortcut)])
        if tip:
            a.setToolTip(tip)
        a.setCheckable(checkable)
        a.triggered.connect(slot)
        return a

    def _build_actions(self) -> None:
        A = self._act
        t = self.tab
        self.a_open = A("Открыть…", self.open_dialog, QKeySequence.Open, "open", "Открыть PDF (⌘O)")
        self.a_merge = A("Объединить файлы…", self.merge_dialog, None, "merge",
                         "Собрать несколько PDF и картинок в один документ")
        self.a_close = A("Закрыть вкладку", lambda: self.close_tab(self.tabs.currentIndex()), QKeySequence.Close)
        self.a_save = A("Сохранить", lambda: t() and t().save(), QKeySequence.Save, "save", "")
        self.a_save_as = A("Сохранить как…", lambda: t() and t().save_as(), QKeySequence("Shift+Ctrl+S"), "saveas",
                           "Сохранить в новый файл, исходный не меняется (⇧⌘S)")
        self.a_save.setToolTip("Сохранить в этот же файл (⌘S)")
        self.a_extract = A("Извлечь выбранные листы…", lambda: t() and t().extract(t().target_pids_any()), None,
                           "extract")
        self.a_split = A("Разделить документ…", lambda: t() and t().split_doc(), None, "split")
        self.a_optimize = A("Оптимизировать PDF…", lambda: t() and t().optimize_dialog(), QKeySequence("Alt+Ctrl+O"),
                            "optimize", "Уменьшить размер: картинки, шрифты, лишние данные (как в Acrobat)")
        self.a_undo = A("Отменить", lambda: t() and t().undo(), QKeySequence.Undo, "undo", "Отменить (⌘Z)")
        self.a_redo = A("Вернуть", lambda: t() and t().redo(), QKeySequence("Shift+Ctrl+Z"), "redo",
                        "Вернуть (⇧⌘Z)")
        self.a_delete = A("Удалить выбранное", self._delete_selected, None)
        self.a_copy = A("Копировать", lambda: t() and t().view.copy_selection(), QKeySequence.Copy)
        self.a_selall = A("Выделить весь текст листа", self._select_all, QKeySequence.SelectAll)
        self.a_find = A("Найти…", lambda: t() and t().open_search(), QKeySequence.Find, "search", "Поиск (⌘F)")
        self.a_find_next = A("Найти далее", lambda: t() and t().search_step(1), QKeySequence("Ctrl+G"))
        self.a_find_prev = A("Найти предыдущее", lambda: t() and t().search_step(-1), QKeySequence("Shift+Ctrl+G"))
        self.a_zoom_in = A("Увеличить", lambda: t() and t().view.zoom_by(1.25), ["Ctrl++", "Ctrl+="], "zoomin",
                           "Увеличить (⌘+)")
        self.a_zoom_out = A("Уменьшить", lambda: t() and t().view.zoom_by(0.8), ["Ctrl+-"], "zoomout",
                            "Уменьшить (⌘−)")
        self.a_zoom_100 = A("Фактический размер", lambda: t() and t().view.set_zoom(1.0), QKeySequence("Ctrl+0"))
        self.a_fit_w = A("По ширине", lambda: t() and t().view.set_fit("width"), QKeySequence("Ctrl+1"),
                         "fitwidth", "По ширине (⌘1)")
        self.a_fit_p = A("Лист целиком", lambda: t() and t().view.set_fit("page"), QKeySequence("Ctrl+2"),
                         "fitpage", "Лист целиком (⌘2)")
        self.a_goto = A("Перейти к листу…", self._goto_dialog, QKeySequence("Alt+Ctrl+G"))
        self.a_thumbs = A("Миниатюры", self._toggle_thumbs, QKeySequence("Alt+Ctrl+1"), checkable=True)
        self.a_thumbs.setChecked(True)
        self.a_side = A("Панель свойств", self._toggle_side, QKeySequence("Alt+Ctrl+2"), checkable=True)
        self.a_side.setChecked(True)
        self.mode_group = QActionGroup(self)
        self.mode_group.setExclusive(True)
        self.a_modes = {}
        for key, text, ic, sc, tip in (
                ("select", "Выбор", "select", "Ctrl+Shift+1",
                 "Выделить текст и скопировать (⌘C), двигать подписи, печати и надписи; "
                 "на пустом месте — тянуть лист «рукой»"),
                ("edit", "Править текст", "edittext", "Ctrl+E", "Наведите на строку и кликните (⌘E)"),
                ("addtext", "Добавить текст", "addtext", "Ctrl+T", "Кликните в любое место листа (⌘T)"),
                ("erase", "Удалить область", "erase", "Ctrl+Shift+E", "Удалить содержимое выделенной области"),
                ("stamp", "Подпись и печать", "stamp", "Ctrl+K", "Библиотека подписей и печатей (⌘K)"),
                ("measure", "Линейка", "ruler", "Ctrl+Shift+M",
                 "Измерить расстояние, длину ломаной или площадь в масштабе чертежа (⇧⌘M)")):
            a = A(text, lambda _=False, k=key: self.set_mode(k), sc, ic, tip, checkable=True)
            self.mode_group.addAction(a)
            self.a_modes[key] = a
        self.a_modes["select"].setChecked(True)
        self.a_organize = A("Систематизировать листы", lambda: self.show_organizer(self.a_organize.isChecked()),
                            QKeySequence("Ctrl+Shift+P"), "grid",
                            "Все листы крупно: переставить, повернуть, удалить, вставить (⇧⌘P)", checkable=True)
        self.a_rot = A("Повернуть по часовой", lambda: t() and t().rotate_pages(t().target_pids_any(), 90),
                       QKeySequence("Ctrl+R"), "rotate", "Повернуть листы по часовой (⌘R)")
        self.a_rotl = A("Повернуть против часовой", lambda: t() and t().rotate_pages(t().target_pids_any(), 270),
                        QKeySequence("Ctrl+L"), "rotateleft")
        self.a_blank = A("Вставить пустой лист", lambda: t() and t().insert_blank(), QKeySequence("Shift+Ctrl+N"),
                         "blank")
        self.a_insert = A("Вставить PDF или картинку…", lambda: t() and t().insert_files_dialog(),
                          QKeySequence("Shift+Ctrl+I"), "insert")
        self.a_delpages = A("Удалить листы", lambda: t() and t().delete_pages(t().target_pids_any()), None, "trash",
                            "Выбранные листы (в колонке миниатюр — клавиша ⌫)")
        self.compress_group = QActionGroup(self)
        self.a_compress = {}
        for k, v in COMPRESS.items():
            a = QAction("Не сжимать" if v is None else v["label"], self)
            a.setCheckable(True)
            a.setChecked(self.ctx.compress_mode() == k)
            a.triggered.connect(lambda _=False, k=k: self.ctx.settings.setValue("compress", k))
            self.compress_group.addAction(a)
            self.a_compress[k] = a

    def _build_menus(self) -> None:
        mb = self.menuBar()
        f = mb.addMenu("Файл")
        f.addAction(self.a_open)
        self.recent_menu = f.addMenu("Открыть недавние")
        self.recent_menu.aboutToShow.connect(self._fill_recent)
        f.addAction(self.a_merge)
        f.addSeparator()
        f.addAction(self.a_close)
        f.addAction(self.a_save)
        f.addAction(self.a_save_as)
        f.addAction(self.a_optimize)
        cm = f.addMenu("Сжимать при сохранении")
        for a in self.a_compress.values():
            cm.addAction(a)
        f.addSeparator()
        f.addAction(self.a_extract)
        f.addAction(self.a_split)
        e = mb.addMenu("Правка")
        e.addAction(self.a_undo)
        e.addAction(self.a_redo)
        e.addSeparator()
        e.addAction(self.a_copy)
        e.addAction(self.a_selall)
        e.addSeparator()
        e.addAction(self.a_delete)
        e.addSeparator()
        e.addAction(self.a_find)
        e.addAction(self.a_find_next)
        e.addAction(self.a_find_prev)
        v = mb.addMenu("Вид")
        for a in (self.a_zoom_in, self.a_zoom_out, self.a_zoom_100, self.a_fit_w, self.a_fit_p):
            v.addAction(a)
        v.addSeparator()
        v.addAction(self.a_goto)
        v.addSeparator()
        v.addAction(self.a_thumbs)
        v.addAction(self.a_side)
        tm = mb.addMenu("Инструменты")
        for a in self.a_modes.values():
            tm.addAction(a)
        pm = mb.addMenu("Листы")
        pm.addAction(self.a_organize)
        pm.addSeparator()
        for a in (self.a_rot, self.a_rotl, self.a_blank, self.a_insert, self.a_delpages):
            pm.addAction(a)
        pm.addSeparator()
        pm.addAction(self.a_extract)
        pm.addAction(self.a_split)
        h = mb.addMenu("Справка")
        h.addAction(self._act("О программе", self._about))

    def _build_toolbar(self) -> None:
        tb = QToolBar("Инструменты")
        tb.setMovable(False)
        tb.setIconSize(QSize(22, 22))
        tb.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
        tb.setObjectName("main")
        self.addToolBar(Qt.TopToolBarArea, tb)
        tb.addAction(self.a_open)
        tb.addAction(self.a_save)
        tb.addAction(self.a_save_as)
        tb.addAction(self.a_optimize)
        tb.addSeparator()
        for a in self.a_modes.values():
            tb.addAction(a)
        tb.addAction(self.a_organize)
        tb.addSeparator()
        tb.addAction(self.a_undo)
        tb.addAction(self.a_redo)
        tb.addSeparator()
        tb.addAction(self.a_zoom_out)
        self.zoom_lbl = QToolButton()
        self.zoom_lbl.setText("100 %")
        self.zoom_lbl.setToolTip("Фактический размер (⌘0)")
        self.zoom_lbl.setMinimumWidth(58)
        self.zoom_lbl.setAutoRaise(True)
        self.zoom_lbl.clicked.connect(self.a_zoom_100.trigger)
        tb.addWidget(self.zoom_lbl)
        tb.addAction(self.a_zoom_in)
        tb.addAction(self.a_fit_w)
        tb.addAction(self.a_fit_p)
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        tb.addWidget(spacer)
        tb.addAction(self.a_find)
        # масштаб, отмена/возврат и поиск — только значками (подсказки остаются)
        for a in (self.a_undo, self.a_redo, self.a_zoom_out, self.a_zoom_in, self.a_fit_w, self.a_fit_p, self.a_find):
            w = tb.widgetForAction(a)
            if w is not None:
                w.setToolButtonStyle(Qt.ToolButtonIconOnly)

        for a, short in ((self.a_organize, "Листы"), (self.a_optimize, "Оптимизировать"),
                         (self.a_modes["edit"], "Править"), (self.a_modes["addtext"], "Текст"),
                         (self.a_modes["erase"], "Стереть"), (self.a_modes["stamp"], "Подпись")):
            a.setIconText(short)
        self.toolbar = tb

    def _build_status(self) -> None:
        sb = self.statusBar()
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 8, 0)
        lay.addWidget(QLabel("Лист"))
        self.page_spin = QSpinBox()
        self.page_spin.setRange(1, 1)
        self.page_spin.setKeyboardTracking(False)
        self.page_spin.valueChanged.connect(lambda v: self.tab() and self.tab().view.go_to_page(v - 1))
        lay.addWidget(self.page_spin)
        self.page_total = QLabel("из 1")
        lay.addWidget(self.page_total)
        sb.addPermanentWidget(w)
        self.status_info = QLabel("")
        sb.addWidget(self.status_info, 1)

    # ------------------------------------------------------------ вкладки

    def tab(self) -> DocTab | None:
        w = self.tabs.currentWidget()
        return w if isinstance(w, DocTab) else None

    def tabs_list(self) -> list[DocTab]:
        return [self.tabs.widget(i) for i in range(self.tabs.count())]

    def add_tab(self, model: DocModel) -> DocTab:
        t = DocTab(self.ctx, model)
        t.titleChanged.connect(lambda t=t: self._update_tab_title(t))
        t.stateChanged.connect(self._update_actions)
        t.toastRequested.connect(self.toast.show_msg)
        t.view.currentPageChanged.connect(lambda *_: self._update_page_box())
        i = self.tabs.addTab(t, model.name)
        self.tabs.setCurrentIndex(i)
        self.stack.setCurrentWidget(self.tabs)
        self._update_tab_title(t)
        t.set_mode(self._current_mode())
        return t

    def _current_mode(self) -> str:
        for k, a in self.a_modes.items():
            if a.isChecked():
                return k
        return "select"

    def _update_tab_title(self, t: DocTab) -> None:
        i = self.tabs.indexOf(t)
        if i < 0:
            return
        name = t.model.name
        self.tabs.setTabText(i, ("● " if t.model.dirty else "") + name)
        self.tabs.setTabToolTip(i, t.model.path or name)
        if t is self.tab():
            self.setWindowTitle(f"{name}[*] — {APP_TITLE}")
            self.setWindowModified(t.model.dirty)
            self.setWindowFilePath(t.model.path or "")

    def _tab_changed(self, i: int) -> None:
        t = self.tab()
        if t is None:
            self.setWindowTitle(APP_TITLE)
            self.setWindowModified(False)
            self.stack.setCurrentWidget(self.empty)
            self.empty.refresh()
        else:
            self._update_tab_title(t)
            t.set_mode(self._current_mode())
            self.a_organize.setChecked(t.organizer_shown())
            t.view.schedule_render()
        self._update_actions()

    def close_tab(self, i: int) -> None:
        if i < 0:
            return
        t = self.tabs.widget(i)
        if isinstance(t, DocTab) and not t.can_close():
            return
        self.tabs.removeTab(i)
        t.deleteLater()
        if self.tabs.count() == 0:
            self.stack.setCurrentWidget(self.empty)
            self.empty.refresh()
        self._update_actions()

    # ------------------------------------------------------------ открытие

    def open_dialog(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Открыть", self.ctx.last_dir(), OPEN_FILTER)
        if paths:
            self.ctx.set_last_dir(paths[0])
            self.open_paths(paths)

    def open_paths(self, paths: list[str]) -> None:
        for p in paths:
            p = os.path.abspath(p)
            already = next((t for t in self.tabs_list() if t.model.path and os.path.exists(t.model.path)
                            and os.path.exists(p) and os.path.samefile(t.model.path, p)), None)
            if already is not None:
                self.tabs.setCurrentWidget(already)
            else:
                try:
                    m = open_model(self, p)
                except Exception as e:
                    QMessageBox.critical(self, "Не получилось открыть файл", f"{os.path.basename(p)}:\n{e}")
                    continue
                if m is None:
                    continue
                self.add_tab(m)
                if m.path:
                    self.ctx.add_recent(m.path)
        self.raise_()
        self.activateWindow()

    def merge_dialog(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Объединить файлы (порядок можно поменять потом)",
                                                self.ctx.last_dir(), OPEN_FILTER)
        if paths:
            self.ctx.set_last_dir(paths[0])
            self.merge_paths(sorted(paths, key=lambda p: os.path.basename(p).lower()))

    def merge_paths(self, paths: list[str]) -> None:
        m = open_model(self, paths[0])
        if m is None:
            return
        base = os.path.splitext(os.path.basename(paths[0]))[0]
        m.path = None
        m.name = f"{base}_объединённый.pdf"
        t = self.add_tab(m)
        t.insert_files(paths[1:], len(m.pages))
        m.reset_history()
        m._saved_at = -1  # новый документ — не сохранён
        self._update_tab_title(t)

    def _fill_recent(self) -> None:
        self.recent_menu.clear()
        for p in self.ctx.recent():
            self.recent_menu.addAction(os.path.basename(p), lambda p=p: self.open_paths([p])).setToolTip(p)
        if not self.ctx.recent():
            a = self.recent_menu.addAction("Пусто")
            a.setEnabled(False)

    # ------------------------------------------------------------ перетаскивание файлов из Finder

    def dragEnterEvent(self, e) -> None:
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e) -> None:
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        paths = [p for p in paths if os.path.isfile(p)]
        if not paths:
            return
        t = self.tab()
        if t is not None and t.mode == "stamp" and all(p.lower().endswith((".png", ".jpg", ".jpeg", ".heic", ".heif"))
                                                       for p in paths):
            for p in paths:
                t.import_scan_file(p)
            return
        e.acceptProposedAction()
        self.open_paths(paths)
        if t is not None:
            self.toast.show_msg("Чтобы вставить файл в документ, бросьте его на колонку миниатюр слева")

    # ------------------------------------------------------------ режимы и состояние

    def show_organizer(self, on: bool) -> None:
        self.a_organize.setChecked(on)
        t = self.tab()
        if t is not None:
            t.show_organizer(on)
        self._update_actions()

    def set_mode(self, mode: str) -> None:
        if self.a_organize.isChecked():
            self.show_organizer(False)
        a = self.a_modes.get(mode)
        if a and not a.isChecked():
            a.setChecked(True)
        t = self.tab()
        if t is not None and t.mode != mode:
            t.set_mode(mode)

    def _select_all(self) -> None:
        t = self.tab()
        if t is None:
            return
        if t.thumbs.hasFocus():
            t.thumbs.selectAll()
            return
        self.set_mode("select")
        t.view.select_all_text()

    def _delete_selected(self) -> None:
        t = self.tab()
        if t is None:
            return
        if not t.view.delete_selected() and t.thumbs.hasFocus():
            t.delete_pages(t.thumbs.selected_pids())

    def _update_page_box(self) -> None:
        t = self.tab()
        n = len(t.model.pages) if t else 1
        cur = t.view.current_page() + 1 if t else 1
        self.page_spin.blockSignals(True)
        self.page_spin.setRange(1, max(1, n))
        self.page_spin.setValue(cur)
        self.page_spin.blockSignals(False)
        self.page_total.setText(f"из {n}")

    def _update_actions(self) -> None:
        t = self.tab()
        has = t is not None
        for a in (self.a_save, self.a_save_as, self.a_close, self.a_copy, self.a_selall, self.a_optimize, self.a_extract, self.a_split, self.a_find,
                  self.a_find_next, self.a_find_prev, self.a_zoom_in, self.a_zoom_out, self.a_zoom_100, self.a_fit_w,
                  self.a_fit_p, self.a_goto, self.a_rot, self.a_rotl, self.a_blank, self.a_insert, self.a_delpages,
                  self.a_delete, self.a_organize, *self.a_modes.values()):
            a.setEnabled(has)
        editing = has and t.view.editing is not None
        self.a_undo.setEnabled(has and (t.model.can_undo() or editing))
        self.a_redo.setEnabled(has and (t.model.can_redo() or editing))
        if has:
            self.zoom_lbl.setText(f"{round(t.view.zoom * 100)} %")
            self._update_page_box()
            src = t.model.main_source
            info = []
            if src.restricted:
                info.append("защищён от изменений")
            if self.ctx.compress_mode() != "none":
                info.append("сжатие при сохранении: " + COMPRESS[self.ctx.compress_mode()]["label"].split(" (")[0].lower())
            self.status_info.setText(" · ".join(info))
            self._update_tab_title(t)
        else:
            self.zoom_lbl.setText("—")
            self.status_info.setText("")

    def _goto_dialog(self) -> None:
        t = self.tab()
        if t is None:
            return
        n = len(t.model.pages)
        v, ok = QInputDialog.getInt(self, "Перейти к листу", f"Номер листа (1–{n}):", t.view.current_page() + 1, 1, n)
        if ok:
            t.view.go_to_page(v - 1)

    def _toggle_thumbs(self) -> None:
        for t in self.tabs_list():
            t.thumbs.setVisible(self.a_thumbs.isChecked())

    def _toggle_side(self) -> None:
        for t in self.tabs_list():
            t.side.setVisible(self.a_side.isChecked())

    def _about(self) -> None:
        import pymupdf
        from PySide6 import __version__ as qtv
        QMessageBox.about(self, "О программе",
                          f"<b>{APP_TITLE}</b><br>Просмотр и правка PDF для Mac. Работает без интернета.<br><br>"
                          f"Движок: MuPDF {pymupdf.mupdf_version} (PyMuPDF {pymupdf.VersionBind}), "
                          f"интерфейс: Qt {qtv}.<br>Шрифты: PT Serif/PT Sans (OFL), Liberation (OFL), "
                          f"osifont (LGPL с исключением для шрифтов).<br>"
                          f"Библиотека подписей: ~/Library/Application Support/PDF-мастерская/")

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        if self.toast.isVisible():
            self.toast.move((self.width() - self.toast.width()) // 2, self.height() - self.toast.height() - 42)

    def closeEvent(self, e) -> None:
        for t in self.tabs_list():
            self.tabs.setCurrentWidget(t)
            if not t.can_close():
                e.ignore()
                return
        self.ctx.settings.setValue("geometry", self.saveGeometry())
        e.accept()
