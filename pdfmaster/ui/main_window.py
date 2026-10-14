"""Главное окно: меню, панель инструментов, вкладки документов."""
from __future__ import annotations

import os
import sys

from PySide6.QtCore import QEvent, QObject, QSettings, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QActionGroup, QGuiApplication, QKeySequence, QPalette, QPixmap
from PySide6.QtWidgets import (QApplication, QFileDialog, QFrame, QHBoxLayout, QInputDialog, QLabel,
                               QMainWindow, QMessageBox, QPushButton, QSizePolicy, QSpinBox, QStackedWidget,
                               QTabBar, QTabWidget, QToolBar, QToolButton, QVBoxLayout, QWidget)

from ..core.library import Library
from ..version import version
from ..core.model import DocModel
from ..core.saver import COMPRESS
from ..render.service import RenderService
from . import icons, theme
from .doc_tab import OPEN_FILTER, DocTab, open_model

APP_TITLE = "PDFMaster"

# Что делать в каждом режиме — одной строкой внизу окна, пока режим включён.
MODE_HINTS = {
    "select": ("Выбор", ("Протяните по тексту — выделение, ⌘C — копировать. Подписи и печати двигаются мышью, "
                         "на пустом месте лист тянется «рукой».")),
    "edit": ("Правка текста", ("Наведите на строку и кликните — правка на месте. Enter или Esc — готово, ⌘Z — вернуть как было. "
                               "Длинный текст сам переносится по абзацу.")),
    "addtext": ("Новый текст", "Кликните в любое место листа и печатайте. Enter — новая строка, Esc — готово."),
    "erase": ("Стереть", "Обведите рамкой, что убрать: текст, картинки и линии внутри рамки удалятся."),
    "stamp": ("Подпись и печать", ("Выберите подпись справа и кликните на листе. Угол — размер, "
                                   "кружок сверху — поворот.")),
    "measure": ("Линейка", ("Кликните в начало и конец. Для ломаной и площади — несколько кликов, "
                            "двойной клик — готово. ⇧ — угол кратно 15°.")),
    "organize": ("Листы", ("Перетаскивайте листы; наведите на лист — повернуть или удалить; «+» между "
                           "листами — вставить; файлы из Finder можно бросить сюда; двойной клик — открыть лист.")),
}


class AppContext(QObject):
    libraryChanged = Signal()
    modeRequested = Signal(str)
    organizerRequested = Signal(bool)

    def __init__(self):
        super().__init__()
        # PDFM_SETTINGS — отдельный файл настроек (автотесты не трогают настройки программы)
        alt = os.environ.get("PDFM_SETTINGS")
        self.settings = QSettings(alt, QSettings.IniFormat) if alt else QSettings("PDFMaster", "PDF-masterskaya")
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
        # «pytest-of-» — следы старых автотестов, писавших в настройки программы
        return [p for p in (v or []) if os.path.exists(p) and "/pytest-of-" not in p]

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
        # ширина — по тексту (не уже), но не больше 70 % окна; высота — по переносу
        fm = self.fontMetrics()
        w = min(int(self.parent().width() * 0.7), fm.horizontalAdvance(msg) + 40)
        self.setFixedWidth(max(160, w))
        self.setFixedHeight(self.heightForWidth(self.width()) if self.hasHeightForWidth() else self.sizeHint().height())
        p = self.parent()
        self.move((p.width() - self.width()) // 2, p.height() - self.height() - 42)
        self.raise_()
        self.show()
        self._t.start(ms)


class DropZone(QFrame):
    """Пунктирная зона «перетащите сюда» на стартовом экране (подсвечивается при перетаскивании)."""

    def __init__(self, win: MainWindow):
        super().__init__()
        self.setObjectName("dropZone")
        self.setMinimumHeight(170)
        self.setCursor(Qt.PointingHandCursor)
        self.win = win
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 22, 24, 22)
        lay.setSpacing(6)
        ic = QLabel()
        ic.setAlignment(Qt.AlignCenter)
        ic.setPixmap(icons.icon("open", on=None).pixmap(QSize(34, 34)))
        t = QLabel("Перетащите PDF сюда")
        t.setObjectName("dropTitle")
        t.setAlignment(Qt.AlignCenter)
        sub = QLabel("или кликните, чтобы выбрать файл · можно сразу несколько — откроются во вкладках")
        sub.setObjectName("dropSub")
        sub.setAlignment(Qt.AlignCenter)
        sub.setWordWrap(True)
        lay.addStretch(1)
        lay.addWidget(ic)
        lay.addWidget(t)
        lay.addWidget(sub)
        lay.addStretch(1)

    def set_active(self, on: bool) -> None:
        self.setProperty("active", "true" if on else "false")
        self.style().unpolish(self)
        self.style().polish(self)

    def mouseReleaseEvent(self, e) -> None:
        if e.button() == Qt.LeftButton:
            self.win.open_dialog()


class EmptyState(QWidget):
    """Стартовый экран: карточка с зоной перетаскивания, кнопками и недавними файлами."""

    def __init__(self, win: "MainWindow"):
        super().__init__()
        self.setObjectName("welcome")
        self.win = win
        outer = QVBoxLayout(self)
        outer.addStretch(1)
        row = QHBoxLayout()
        row.addStretch(1)
        card = QFrame()
        card.setObjectName("welcomeCard")
        card.setMinimumWidth(560)
        card.setMaximumWidth(640)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(36, 32, 36, 28)
        lay.setSpacing(0)
        head = QHBoxLayout()
        logo = QLabel()
        from ..core.fonts import resource_dir
        pm = QPixmap(os.path.join(resource_dir(), "icon.png"))
        if not pm.isNull():
            dpr = self.devicePixelRatioF() or 2.0
            pm = pm.scaled(int(56 * dpr), int(56 * dpr), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            pm.setDevicePixelRatio(dpr)
            logo.setPixmap(pm)
        head.addWidget(logo)
        head.addSpacing(14)
        tt = QVBoxLayout()
        tt.setSpacing(2)
        t = QLabel(APP_TITLE)
        t.setObjectName("welcomeTitle")
        sub = QLabel("Правка PDF прямо на месте: текст, листы, подписи и печати, линейка для чертежей.\n"
                     "Всё работает на этом Mac, файлы никуда не отправляются.")
        sub.setObjectName("welcomeSub")
        sub.setWordWrap(True)
        tt.addWidget(t)
        tt.addWidget(sub)
        head.addLayout(tt, 1)
        lay.addLayout(head)
        lay.addSpacing(22)
        self.drop = DropZone(win)
        lay.addWidget(self.drop)
        lay.addSpacing(16)
        btns = QHBoxLayout()
        b = QPushButton(icons.icon("open", white=True), "  Открыть PDF…")
        b.setProperty("primary", True)
        b.setCursor(Qt.PointingHandCursor)
        b.clicked.connect(win.open_dialog)
        m = QPushButton(icons.icon("merge"), "  Объединить файлы…")
        m.setCursor(Qt.PointingHandCursor)
        m.clicked.connect(win.merge_dialog)
        btns.addWidget(b)
        btns.addWidget(m)
        btns.addStretch(1)
        lay.addLayout(btns)
        self.recent_title = QLabel("НЕДАВНИЕ")
        self.recent_title.setProperty("role", "section")
        lay.addSpacing(20)
        lay.addWidget(self.recent_title)
        lay.addSpacing(6)
        self.recent_box = QVBoxLayout()
        self.recent_box.setSpacing(2)
        lay.addLayout(self.recent_box)
        row.addWidget(card)
        row.addStretch(1)
        outer.addLayout(row)
        outer.addStretch(2)

    def refresh(self) -> None:
        while self.recent_box.count():
            it = self.recent_box.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        rec = self.win.ctx.recent()[:5]
        self.recent_title.setVisible(bool(rec))
        home = os.path.expanduser("~")
        for p in rec:
            folder = os.path.dirname(p)
            if folder.startswith(home):
                folder = "~" + folder[len(home):]
            b = QPushButton(icons.icon("page"), f"  {os.path.basename(p)}    ·  {folder}")
            b.setProperty("recent", True)
            b.setToolTip(p)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, p=p: self.win.open_paths([p]))
            self.recent_box.addWidget(b)


class MainWindow(QMainWindow):
    def __init__(self, ctx: AppContext):
        super().__init__()
        self.ctx = ctx
        self.quitting = False   # выход из программы (⌘Q), а не просто закрытие окна
        theme.apply(QApplication.instance())
        self.setWindowTitle(APP_TITLE)
        self.setUnifiedTitleAndToolBarOnMac(True)
        self.setAcceptDrops(True)
        self.resize(1400, 900)
        g = ctx.settings.value("geometry")
        if g is not None:
            self.restoreGeometry(g)
        self.stack = QStackedWidget()
        self.stack.setObjectName("rootStack")
        self.tabs = QTabWidget()
        self.tabs.setObjectName("docTabs")
        self.tabs.setDocumentMode(True)
        self.tabs.setTabsClosable(True)
        self.tabs.setMovable(True)
        self.tabs.setElideMode(Qt.ElideMiddle)
        self.tabs.tabBar().setDrawBase(False)
        self.tabs.tabBar().setExpanding(False)
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
        self._update_mode_hint()

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
        self.a_print = A("Печать…", self._print, QKeySequence.Print, "print", "Распечатать (⌘P)")
        self.a_updates = A("Проверить обновления…", self._check_updates, None)
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
        self._mode_icons: dict[str, str] = {}
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
            a.setIcon(icons.icon(ic, on=theme.tokens()["accent"]))
            self._mode_icons[key] = ic
            self.mode_group.addAction(a)
            self.a_modes[key] = a
        self.a_modes["select"].setChecked(True)
        self.a_organize = A("Систематизировать листы", lambda: self.show_organizer(self.a_organize.isChecked()),
                            QKeySequence("Ctrl+Shift+P"), "grid",
                            "Все листы крупно: переставить, повернуть, удалить, вставить (⇧⌘P)", checkable=True)
        self.a_organize.setIcon(icons.icon("grid", on=theme.tokens()["accent"]))
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
        f.addAction(self.a_print)
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
        h.addAction(self.a_updates)
        h.addAction(self._act("О программе", self._about))

    def _pill(self, *items) -> QFrame:
        """Группа маленьких кнопок в общей «таблетке» (масштаб, отмена/возврат)."""
        f = QFrame()
        f.setObjectName("pill")
        lay = QHBoxLayout(f)
        lay.setContentsMargins(3, 2, 3, 2)
        lay.setSpacing(0)
        for it in items:
            if isinstance(it, QAction):
                b = QToolButton()
                b.setDefaultAction(it)
                b.setIconSize(QSize(18, 18))
                b.setToolButtonStyle(Qt.ToolButtonIconOnly)
                lay.addWidget(b)
            else:
                lay.addWidget(it)
        return f

    def _build_toolbar(self) -> None:
        tb = QToolBar("Инструменты")
        tb.setMovable(False)
        tb.setIconSize(QSize(20, 20))
        tb.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
        tb.setObjectName("main")
        self.addToolBar(Qt.TopToolBarArea, tb)
        # файл: «Сохранить» с меню (как… / сжать) — три кнопки в одной
        tb.addAction(self.a_open)
        tb.addAction(self.a_save)
        from PySide6.QtWidgets import QMenu
        sm = QMenu(self)
        sm.addAction(self.a_save_as)
        sm.addAction(self.a_optimize)
        sm.addSeparator()
        cm = sm.addMenu("Сжимать при сохранении")
        for a in self.a_compress.values():
            cm.addAction(a)
        w = tb.widgetForAction(self.a_save)
        if w is not None:
            w.setMenu(sm)
            w.setPopupMode(QToolButton.MenuButtonPopup)
        self.save_menu = sm
        tb.addSeparator()
        # режимы — одна группа, активный подсвечен фирменным цветом
        for a in self.a_modes.values():
            tb.addAction(a)
        tb.addAction(self.a_organize)
        tb.addSeparator()
        tb.addWidget(self._pill(self.a_undo, self.a_redo))
        gap = QWidget()
        gap.setFixedWidth(8)
        tb.addWidget(gap)
        self.zoom_lbl = QToolButton()
        self.zoom_lbl.setObjectName("zoomLbl")
        self.zoom_lbl.setText("100 %")
        self.zoom_lbl.setToolTip("Фактический размер (⌘0)")
        self.zoom_lbl.setMinimumWidth(54)
        self.zoom_lbl.setAutoRaise(True)
        self.zoom_lbl.clicked.connect(self.a_zoom_100.trigger)
        tb.addWidget(self._pill(self.a_zoom_out, self.zoom_lbl, self.a_zoom_in, self.a_fit_w, self.a_fit_p))
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        tb.addWidget(spacer)
        tb.addAction(self.a_print)
        tb.addAction(self.a_find)
        for a in (self.a_print, self.a_find):
            w = tb.widgetForAction(a)
            if w is not None:
                w.setToolButtonStyle(Qt.ToolButtonIconOnly)
        for a, short in ((self.a_organize, "Листы"), (self.a_modes["edit"], "Править"),
                         (self.a_modes["addtext"], "Текст"), (self.a_modes["erase"], "Стереть"),
                         (self.a_modes["stamp"], "Подпись")):
            a.setIconText(short)
        for a in (*self.a_modes.values(), self.a_organize, self.a_open, self.a_save):
            w = tb.widgetForAction(a)
            if w is not None:
                w.setCursor(Qt.PointingHandCursor)
                w.setMinimumWidth(58)
        self.toolbar = tb

    def _build_status(self) -> None:
        sb = self.statusBar()
        sb.setSizeGripEnabled(False)
        left = QWidget()
        ll = QHBoxLayout(left)
        ll.setContentsMargins(8, 2, 0, 2)
        ll.setSpacing(6)
        self.mode_name = QLabel("")
        self.mode_name.setObjectName("modeName")
        self.mode_hint = QLabel("")
        self.mode_hint.setObjectName("modeHint")
        self.mode_hint.setMinimumWidth(10)
        self.mode_hint.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        ll.addWidget(self.mode_name)
        ll.addWidget(self.mode_hint, 1)
        sb.addWidget(left, 1)
        self.status_info = QLabel("")
        sb.addPermanentWidget(self.status_info)
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 8, 0)
        lay.setSpacing(4)
        prev = QToolButton()
        prev.setIcon(icons.icon("up"))
        prev.setToolTip("Предыдущий лист")
        prev.clicked.connect(lambda: self.tab() and self.tab().view.go_to_page(max(0, self.tab().view.current_page() - 1)))
        nxt = QToolButton()
        nxt.setIcon(icons.icon("down"))
        nxt.setToolTip("Следующий лист")
        nxt.clicked.connect(lambda: self.tab() and self.tab().view.go_to_page(
            min(len(self.tab().model.pages) - 1, self.tab().view.current_page() + 1)))
        lay.addWidget(QLabel("Лист"))
        lay.addWidget(prev)
        self.page_spin = QSpinBox()
        self.page_spin.setRange(1, 1)
        self.page_spin.setKeyboardTracking(False)
        self.page_spin.setAlignment(Qt.AlignCenter)
        self.page_spin.setMinimumWidth(46)
        self.page_spin.setFixedHeight(22)
        self.page_spin.setObjectName("pageSpin")
        self.page_spin.valueChanged.connect(lambda v: self.tab() and self.tab().view.go_to_page(v - 1))
        lay.addWidget(self.page_spin)
        lay.addWidget(nxt)
        self.page_total = QLabel("из 1")
        lay.addWidget(self.page_total)
        sb.addPermanentWidget(w)
        self.page_nav = w

    def _update_mode_hint(self) -> None:
        t = self.tab()
        if t is None:
            self.mode_name.hide()
            self.mode_hint.setText("")
            return
        key = "organize" if self.a_organize.isChecked() else self._current_mode()
        name, hint = MODE_HINTS.get(key, ("", ""))
        self.mode_name.setText(name)
        self.mode_name.setVisible(bool(name))
        self.mode_hint.setText(hint)
        self.mode_hint.setToolTip(hint)

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
        # своя кнопка закрытия: крупнее стандартной, с подсветкой при наведении
        close = QToolButton()
        close.setObjectName("tabClose")
        close.setIcon(icons.icon("close"))
        close.setIconSize(QSize(12, 12))
        close.setFixedSize(22, 22)
        close.setCursor(Qt.PointingHandCursor)
        close.setToolTip("Закрыть вкладку (⌘W)")
        close.clicked.connect(lambda _=False, t=t: self.close_tab(self.tabs.indexOf(t)))
        self.tabs.tabBar().setTabButton(i, QTabBar.RightSide, close)
        self.tabs.setCurrentIndex(i)
        self.stack.setCurrentWidget(self.tabs)
        self._update_tab_title(t)
        t.set_mode(self._current_mode())
        return t

    def _current_mode(self) -> str:
        if getattr(self, "_org_on", False):
            return getattr(self, "_mode_before_org", "select")
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
            self.show_organizer(t.organizer_shown())
            t.view.schedule_render()
        self._update_actions()
        self._update_mode_hint()

    def close_tab(self, i: int) -> None:
        if i < 0:
            return
        t = self.tabs.widget(i)
        if isinstance(t, DocTab) and not t.can_close():
            return
        self._drop_tab(i)

    def _drop_tab(self, i: int) -> None:
        t = self.tabs.widget(i)
        self.tabs.removeTab(i)
        t.deleteLater()
        if self.tabs.count() == 0:
            self.stack.setCurrentWidget(self.empty)
            self.empty.refresh()
        self._update_actions()
        self._update_mode_hint()

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
        if not self.isVisible():
            self.show()
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
            if self.stack.currentWidget() is self.empty:
                self.empty.drop.set_active(True)

    def dragLeaveEvent(self, e) -> None:
        self.empty.drop.set_active(False)
        super().dragLeaveEvent(e)

    def dropEvent(self, e) -> None:
        self.empty.drop.set_active(False)
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
        # в режиме «Листы» инструменты листа не подсвечиваем, после выхода — возвращаем прежний
        if on and not getattr(self, "_org_on", False):
            self._mode_before_org = self._current_mode()
            self.mode_group.setExclusionPolicy(QActionGroup.ExclusionPolicy.ExclusiveOptional)
            a = self.a_modes.get(self._mode_before_org)
            if a is not None:
                a.setChecked(False)
        elif not on and getattr(self, "_org_on", False):
            a = self.a_modes.get(getattr(self, "_mode_before_org", "select"))
            if a is not None:
                a.setChecked(True)
            self.mode_group.setExclusionPolicy(QActionGroup.ExclusionPolicy.Exclusive)
        self._org_on = on
        self.a_organize.setChecked(on)
        t = self.tab()
        if t is not None:
            t.show_organizer(on)
        self._update_actions()
        self._update_mode_hint()

    def set_mode(self, mode: str) -> None:
        if self.a_organize.isChecked():
            self.show_organizer(False)
        a = self.a_modes.get(mode)
        if a and not a.isChecked():
            a.setChecked(True)
        t = self.tab()
        if t is not None and t.mode != mode:
            t.set_mode(mode)
        self._update_mode_hint()

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
        for a in (self.a_print, self.a_save, self.a_save_as, self.a_close, self.a_copy, self.a_selall, self.a_optimize, self.a_extract, self.a_split, self.a_find,
                  self.a_find_next, self.a_find_prev, self.a_zoom_in, self.a_zoom_out, self.a_zoom_100, self.a_fit_w,
                  self.a_fit_p, self.a_goto, self.a_rot, self.a_rotl, self.a_blank, self.a_insert, self.a_delpages,
                  self.a_delete, self.a_organize, *self.a_modes.values()):
            a.setEnabled(has)
        if hasattr(self, "page_nav"):
            self.page_nav.setVisible(has)
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

    def _print(self) -> None:
        t = self.tab()
        if t is not None:
            from .printing import print_document
            print_document(t)

    def _check_updates(self) -> None:
        if not hasattr(self, "_updater"):
            from .updates import Updater
            self._updater = Updater(self)
        self._updater.check()

    def _about(self) -> None:
        import pymupdf
        from PySide6 import __version__ as qtv
        QMessageBox.about(self, "О программе",
                          f"<b>{APP_TITLE}</b> {version()}<br>Просмотр и правка PDF. Работает без интернета "
                          f"(в сеть — только по кнопке «Проверить обновления»).<br><br>"
                          f"Движок: MuPDF {pymupdf.mupdf_version} (PyMuPDF {pymupdf.VersionBind}), "
                          f"интерфейс: Qt {qtv}.<br>Шрифты: PT Serif/PT Sans (OFL), Liberation (OFL), "
                          f"osifont (LGPL с исключением для шрифтов).<br>"
                          f"Библиотека подписей: {Library().root}")

    def changeEvent(self, e) -> None:
        super().changeEvent(e)
        if e.type() in (QEvent.ApplicationPaletteChange, QEvent.ThemeChange) and not getattr(self, "_retheming", False):
            # светлая/тёмная тема macOS переключилась — перекрасить всё
            self._retheming = True
            try:
                icons.clear_cache()
                theme.apply(QApplication.instance())
                acc = theme.tokens()["accent"]
                for k, a in self.a_modes.items():
                    a.setIcon(icons.icon(self._mode_icons[k], on=acc))
                self.a_organize.setIcon(icons.icon("grid", on=acc))
            finally:
                self._retheming = False

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
        if sys.platform == "darwin" and not self.quitting:
            # как принято на Mac: окно закрывается вместе с документами, программа остаётся в Dock
            # (значок в Dock открывает окно снова, совсем выйти — ⌘Q)
            e.ignore()
            while self.tabs.count():
                self._drop_tab(0)
            self.hide()
            return
        e.accept()
