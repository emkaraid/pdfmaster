"""Вкладка документа: модель + просмотр + миниатюры + панель свойств."""
from __future__ import annotations

import os

from PySide6.QtCore import QPointF, Qt, QTimer, Signal
from PySide6.QtGui import QIcon, QImage, QPainter, QPixmap
from PySide6.QtWidgets import (QApplication, QFileDialog, QFrame, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
                               QMessageBox, QProgressDialog, QSplitter, QToolButton, QVBoxLayout,
                               QWidget)

from ..core import images, ops
from ..core.model import DocModel, NeedPassword, WrongPassword
from ..core.textlines import color_int_to_rgb, rgb_to_hex
from . import icons
from .dialogs import SignatureDialog, SplitDialog, StampImportDialog
from .page_view import PageView
from .side_panel import SidePanel
from .thumbs import ThumbPanel

PDF_FILTER = "PDF (*.pdf)"
OPEN_FILTER = "PDF и картинки (*.pdf *.jpg *.jpeg *.png *.heic *.heif *.tif *.tiff);;PDF (*.pdf);;Все файлы (*)"


def ask_password(parent, name: str, wrong: bool = False) -> str | None:
    text, ok = QInputDialog.getText(parent, "Пароль", ("Неверный пароль. " if wrong else "") +
                                    f"Файл «{name}» защищён паролем на открытие.\nВведите пароль:",
                                    QLineEdit.Password)
    return text if ok else None


def open_model(parent, path: str) -> DocModel | None:
    pw = None
    wrong = False
    while True:
        try:
            return DocModel.open(path, password=pw)
        except NeedPassword:
            pass
        except WrongPassword:
            wrong = True
        pw = ask_password(parent, os.path.basename(path), wrong)
        if pw is None:
            return None


class Banner(QFrame):
    def __init__(self, text: str, kind: str = "warn"):
        super().__init__()
        bg = {"warn": "#fff4d6", "info": "#e5efff"}[kind]
        self.setStyleSheet(f"QFrame {{ background: {bg}; border-bottom: 1px solid rgba(0,0,0,0.08); }}"
                           "QLabel { color: #3a3226; background: transparent; }")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 6, 6, 6)
        lb = QLabel(text)
        lb.setWordWrap(True)
        lay.addWidget(lb, 1)
        x = QToolButton()
        x.setIcon(icons.icon("close"))
        x.setAutoRaise(True)
        x.clicked.connect(self.hide)
        lay.addWidget(x)


class SearchBar(QFrame):
    def __init__(self, tab: "DocTab"):
        super().__init__()
        self.tab = tab
        self.setObjectName("searchBar")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 4, 6, 4)
        self.edit = QLineEdit()
        self.edit.setPlaceholderText("Найти в документе")
        self.edit.setClearButtonEnabled(True)
        self.edit.returnPressed.connect(self._enter)
        self.count = QLabel("")
        self.count.setMinimumWidth(90)
        prev = QToolButton()
        prev.setIcon(icons.icon("up"))
        prev.setToolTip("Предыдущее (⇧⌘G)")
        prev.clicked.connect(lambda: tab.search_step(-1))
        nxt = QToolButton()
        nxt.setIcon(icons.icon("down"))
        nxt.setToolTip("Следующее (⌘G)")
        nxt.clicked.connect(lambda: tab.search_step(1))
        close = QToolButton()
        close.setIcon(icons.icon("close"))
        close.setAutoRaise(True)
        close.clicked.connect(tab.close_search)
        lay.addWidget(QLabel("Поиск:"))
        lay.addWidget(self.edit, 1)
        lay.addWidget(self.count)
        lay.addWidget(prev)
        lay.addWidget(nxt)
        lay.addWidget(close)
        self._last = ""

    def _enter(self) -> None:
        t = self.edit.text().strip()
        mods = QApplication.keyboardModifiers()
        if t and t == self._last:
            self.tab.search_step(-1 if mods & Qt.ShiftModifier else 1)
        else:
            self._last = t
            self.tab.search(t)

    def keyPressEvent(self, e) -> None:
        if e.key() == Qt.Key_Escape:
            self.tab.close_search()
            return
        super().keyPressEvent(e)


class DocTab(QWidget):
    titleChanged = Signal()
    stateChanged = Signal()          # undo/redo, режим, выделение — обновить кнопки
    toastRequested = Signal(str)

    def __init__(self, app_ctx, model: DocModel):
        super().__init__()
        self.ctx = app_ctx
        self.service = app_ctx.service
        self.library = app_ctx.library
        self.model = model
        self.dark = app_ctx.dark
        self.mode = "select"
        self.last_text_style = {"family": "serif", "size": 12.0, "bold": False, "italic": False, "color": "#000000"}
        self._lines: dict = {}
        self._lines_pending: set = set()
        self._assets_q: dict[str, QImage] = {}
        self._picked: str | None = None
        self._search = {"query": "", "results": {}, "flat": [], "i": -1}
        self._restricted_ok = False
        self._saving = False
        self._register_sources()

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.banners = QVBoxLayout()
        self.banners.setSpacing(0)
        root.addLayout(self.banners)
        self.searchbar = SearchBar(self)
        self.searchbar.hide()
        root.addWidget(self.searchbar)
        self.split = QSplitter(Qt.Horizontal)
        self.split.setChildrenCollapsible(False)
        self.split.setHandleWidth(1)
        self.split.setStyleSheet("QSplitter::handle { background: palette(midlight); }")
        self.thumbs = ThumbPanel(self)
        self.view = PageView(self)
        self.side = SidePanel()
        self.split.addWidget(self.thumbs)
        self.split.addWidget(self.view)
        self.split.addWidget(self.side)
        self.split.setStretchFactor(0, 0)
        self.split.setStretchFactor(1, 1)
        self.split.setStretchFactor(2, 0)
        self.split.setSizes([180, 900, 320])
        from PySide6.QtWidgets import QStackedWidget
        from .organizer import Organizer
        self.center = QStackedWidget()
        self.center.addWidget(self.split)
        self.organizer = Organizer(self)
        self.center.addWidget(self.organizer)
        root.addWidget(self.center, 1)

        src = model.main_source
        if src.restricted:
            self._add_banner("Файл защищён от изменений (есть ограничения без пароля на открытие). Править можно; "
                             "при сохранении ограничения будут сняты.")
        if src.user_password:
            self.banners.addWidget(Banner("Файл защищён паролем на открытие — при сохранении пароль сохранится.",
                                          "info"))
        if src.signed:
            self._add_banner("Документ подписан электронной подписью. После любых изменений подпись станет "
                             "недействительной.")

        # сигналы
        self.view.currentPageChanged.connect(self._on_current_page)
        self.view.selectionChanged.connect(self.update_props)
        self.view.editingChanged.connect(self.update_props)
        self.view.zoomChanged.connect(lambda *_: self.stateChanged.emit())
        self.thumbs.pageActivated.connect(self.view.go_to_page)
        self.thumbs.orderChanged.connect(self._on_thumb_order)
        self.thumbs.filesDropped.connect(self.insert_files)
        self.thumbs.contextAction.connect(self._thumb_action)
        self.thumbs.selectionPids.connect(lambda *_: self.update_props())
        sp = self.side
        sp.text.changed.connect(self._text_style_changed)
        sp.text.paragraph.connect(self.view.toggle_paragraph)
        sp.text.revert.connect(self.view.revert_edit)
        sp.stamps.importScan.connect(self.import_scan)
        sp.stamps.drawSignature.connect(self.draw_signature)
        sp.stamps.picked.connect(self._pick_asset)
        sp.stamps.removeItem.connect(self._remove_lib_item)
        sp.stamps.toPages.connect(self.stamp_to_pages)
        sp.stamps.rotate.connect(self.rotate_selected_obj)
        sp.stamps.deleteObj.connect(self.view.delete_selected)
        sp.pages.action.connect(self._page_action)
        sp.erase.whiteChanged.connect(self._erase_white)
        sp.erase.deleteObj.connect(self.view.delete_selected)
        from .measure_tool import MeasureTool
        self.measure = MeasureTool(self, sp.measure)
        self.view.zoomChanged.connect(lambda *_: self.measure.rebuild())
        self.ctx.libraryChanged.connect(self.refresh_library)
        model.listeners.append(self._model_changed)

        self.refresh_library()
        self.view.rebuild()
        self.thumbs.rebuild()
        self.thumbs.set_current(0)
        self.organizer.rebuild()
        self.update_props()
        QTimer.singleShot(0, lambda: self.view.set_fit("width"))

    def schedule_para_hint(self) -> None:
        if not hasattr(self, "_para_timer"):
            self._para_timer = QTimer(self, singleShot=True, interval=250, timeout=self._para_hint)
        self._para_timer.start()

    def _para_hint(self) -> None:
        """Сколько строк займёт абзац — предупреждаем, если он налезет на текст ниже."""
        sess = self.view.editing
        if sess is None or sess.kind != "para":
            return
        from ..core.bake import para_rows
        o = sess.obj
        text = sess.editor.toPlainText()
        ref = self.model.page_ref(sess.pid)
        try:
            rows, size = para_rows(self.model.src_page(ref), o["lines"], text, o.get("style"))
        except Exception:
            return
        n0 = len(o["lines"])
        st = o.get("style") or {}
        if st.get("fit") and rows <= n0:
            span = o["lines"][0]["spans"][o["lines"][0]["chars"][0][7]]
            base = float(st.get("size") or span["size"])
            self.side.text.set_hint(f"Помещается в {n0} стр.: кегль {size:g} вместо {base:g}." if size < base - 0.01
                                    else f"Строк: {rows} из {n0}.")
            self.view.set_editor_size(size)
        elif rows > n0:
            self.side.text.set_hint(f"Абзац стал длиннее: {rows} строк вместо {n0} — нижние строки наползут "
                                    f"на текст ниже. Сократите текст или включите «Уместить в прежние строки».",
                                    warn=True)
            self.view.set_editor_size(None)
        else:
            self.side.text.set_hint(f"Строк: {rows} из {n0}.")
            self.view.set_editor_size(None)

    def measure_click(self, page, lp, e) -> None:
        self.measure.click(page, lp, e)

    def measure_move(self, page, lp, e) -> None:
        self.measure.move(page, lp, e)

    def measure_finish(self) -> None:
        self.measure.finish()

    def show_organizer(self, on: bool) -> None:
        if on:
            self.view.finish_edit(True)
            self.organizer.grid.select_pids(self.thumbs.selected_pids())
            self.center.setCurrentWidget(self.organizer)
            self.organizer.grid.setFocus()
        else:
            self.thumbs.select_pids(self.organizer.grid.selected_pids())
            self.center.setCurrentWidget(self.split)
            self.view.schedule_render()

    def organizer_shown(self) -> bool:
        return self.center.currentWidget() is self.organizer

    def target_pids_any(self) -> list[str]:
        if self.organizer_shown():
            return self.organizer.grid.selected_pids() or self.target_pids()
        return self.target_pids()

    def _add_banner(self, text: str) -> None:
        self.banners.addWidget(Banner(text, "warn"))

    # ------------------------------------------------------------ служебное

    def _register_sources(self) -> None:
        for sid, s in self.model.sources.items():
            self.service.add_source(sid, s.name, s.data, s.password)
        for aid, png in self.model.assets.items():
            self.service.add_asset(aid, png)

    def toast(self, msg: str) -> None:
        self.toastRequested.emit(msg)

    def title(self) -> str:
        return self.model.name

    def asset_image(self, aid: str) -> QImage | None:
        img = self._assets_q.get(aid)
        if img is None:
            png = self.model.assets.get(aid)
            if png is None:
                return None
            img = QImage.fromData(png)
            self._assets_q[aid] = img
        return img

    def _model_changed(self) -> None:
        self.titleChanged.emit()
        self.stateChanged.emit()

    def page_rendered(self, pid: str) -> None:
        pass

    # ------------------------------------------------------------ строки текста

    def lines_cached(self, pid: str):
        ref = self.model.page_ref(pid)
        if ref is None:
            return None
        return self._lines.get((ref["src"], ref["idx"]))

    def prefetch_lines(self, pids) -> None:
        for pid in pids:
            ref = self.model.page_ref(pid)
            if ref is None:
                continue
            k = (ref["src"], ref["idx"])
            if k in self._lines or k in self._lines_pending:
                continue
            self._lines_pending.add(k)

            def done(res, k=k):
                self._lines_pending.discard(k)
                self._lines[k] = res[2]

            def fail(msg, k=k):
                self._lines_pending.discard(k)
                self._lines[k] = []

            self.service.submit("lines", k, sids=[k[0]], priority=-5, callback=done, errback=fail)

    def view_lines(self, pid: str):
        """Строки листа такими, как он виден (с правками) — для выделения текста."""
        ref = self.model.page_ref(pid)
        if ref is None:
            return None
        objs = [o for o in self.model.page_objs(pid) if o.get("t") in ("edit", "text", "erase")]
        if not objs:
            lc = self.lines_cached(pid)
            if lc is None:
                self.prefetch_lines([pid])
            return lc
        import hashlib
        import json
        k = (ref["src"], ref["idx"], hashlib.sha1(json.dumps(objs, sort_keys=True, ensure_ascii=False)
                                                  .encode()).hexdigest())
        if k in self._lines:
            return self._lines[k]
        if k not in self._lines_pending:
            self._lines_pending.add(k)

            def done(res, k=k):
                self._lines_pending.discard(k)
                self._lines[k] = res[2]

            def fail(msg, k=k):
                self._lines_pending.discard(k)
                self._lines[k] = []

            self.service.submit("lines", (ref["src"], ref["idx"], objs), sids=[ref["src"]], priority=-5,
                                callback=done, errback=fail)
        return None

    # ------------------------------------------------------------ изменения модели

    def after_change(self, pages=None, structure: bool = False, quiet: bool = False) -> None:
        if structure:
            self.view.rebuild()
            self.measure.rebuild()
            self.thumbs.rebuild()
            self.thumbs.set_current(self.view.current_page())
            self.organizer.rebuild()
        else:
            self.view.refresh_pages(pages)
            self.thumbs.refresh()
            self.organizer.grid.refresh()
        if self._search["query"] and not quiet:
            QTimer.singleShot(300, lambda: self.search(self._search["query"], keep_pos=True))
        self.update_props()
        self.titleChanged.emit()
        self.stateChanged.emit()

    def undo(self) -> None:
        ed = self.view.editing
        if ed is not None and ed.editor is not None:
            if ed.editor.document().isUndoAvailable():
                ed.editor.document().undo()
                return
            self.view.finish_edit(True)
        before = [(p["id"], p["rot"]) for p in self.model.pages]
        if self.model.undo():
            after = [(p["id"], p["rot"]) for p in self.model.pages]
            self.view.select(None)
            self.after_change(structure=before != after)

    def redo(self) -> None:
        ed = self.view.editing
        if ed is not None and ed.editor is not None:
            if ed.editor.document().isRedoAvailable():
                ed.editor.document().redo()
            return
        before = [(p["id"], p["rot"]) for p in self.model.pages]
        if self.model.redo():
            after = [(p["id"], p["rot"]) for p in self.model.pages]
            self.view.select(None)
            self.after_change(structure=before != after)

    # ------------------------------------------------------------ режимы и панель

    def set_mode(self, mode: str) -> None:
        if self.mode == "measure" and mode != "measure":
            self.measure.cancel()
        self.mode = mode
        self.view.set_mode(mode)
        # что делать в режиме — подсказывает строка внизу окна; всплывает только то, чего там нет
        if mode == "stamp" and not self.library.items():
            self.toast("Загрузите скан печати или нарисуйте подпись в панели справа")
        self.update_props()
        self.stateChanged.emit()

    def update_props(self) -> None:
        self._update_props()
        self.stateChanged.emit()

    def _update_props(self) -> None:
        sp = self.side
        sess = self.view.editing
        if sess is not None:
            o = sess.obj
            if sess.kind == "text":
                sp.text.load(kind="text", family=o.get("family", "serif"), size=float(o.get("size", 12)),
                             bold=bool(o.get("bold")), italic=bool(o.get("italic")), color=o.get("color", "#000000"))
            else:
                ln = o["lines"][0]
                span = ln["spans"][ln["chars"][0][7]]
                st = o.get("style") or {}
                from ..core import fonts as F
                hint = ""
                if o.get("para"):
                    hint = "Текст переносится по ширине абзаца."
                sp.text.load(kind="para" if o.get("para") else "line", family=st.get("family", "orig"),
                             fit=bool(st.get("fit")),
                             size=float(st.get("size") or span["size"]),
                             bold=bool(st.get("bold", F.guess_bold(span["font"], span["flags"]))),
                             italic=bool(st.get("italic", F.guess_italic(span["font"], span["flags"]))),
                             color=st.get("color") or rgb_to_hex(color_int_to_rgb(span["color"])),
                             doc_font=span["font"], hint=hint)
            sp.show_page("text")
            if o.get("para"):
                self.schedule_para_hint()
            return
        pid, o = self.view.selected_obj()
        if o is not None and o["t"] == "image":
            sp.stamps.show_obj(o)
            sp.show_page("stamps")
            return
        if o is not None and o["t"] == "text":
            sp.text.load(kind="text", family=o.get("family", "serif"), size=float(o.get("size", 12)),
                         bold=bool(o.get("bold")), italic=bool(o.get("italic")), color=o.get("color", "#000000"))
            sp.show_page("text")
            return
        if o is not None and o["t"] == "erase":
            sp.erase.show_obj(o)
            sp.show_page("erase")
            return
        if self.mode == "measure":
            sp.show_page("measure")
        elif self.mode == "stamp":
            sp.stamps.show_obj(None)
            sp.show_page("stamps")
        elif self.mode == "erase":
            sp.erase.show_obj(None)
            sp.show_page("erase")
        elif self.mode == "addtext":
            st = self.last_text_style
            sp.text.load(kind="text", family=st["family"], size=st["size"], bold=st["bold"], italic=st["italic"],
                         color=st["color"])
            sp.show_page("text")
        else:
            sp.pages.set_info(len(self.thumbs.selected_pids()), self.view.current_page(), len(self.model.pages))
            sp.show_page("pages")

    def _text_style_changed(self, ch: dict) -> None:
        if self.view.editing is not None:
            self.view.apply_style_to_edit(**ch)
            if self.view.editing.kind == "text":
                self.last_text_style.update({k: v for k, v in ch.items() if k in self.last_text_style})
            return
        pid, o = self.view.selected_obj()
        if o is not None and o["t"] == "text":
            o.update(ch)
            self.last_text_style.update({k: v for k, v in ch.items() if k in self.last_text_style})
            self.model.commit()
            self.after_change(pages=[pid])
            return
        self.last_text_style.update({k: v for k, v in ch.items() if k in self.last_text_style})

    def _erase_white(self, on: bool) -> None:
        pid, o = self.view.selected_obj()
        if o is not None and o["t"] == "erase":
            o["white"] = on
            self.model.commit()
            self.after_change(pages=[pid])

    def _on_current_page(self, i: int) -> None:
        self.thumbs.set_current(i)
        if self.mode == "edit":
            self.prefetch_lines([it.pid for it, _ in self.view.visible_pages()])
        if self.side.stack.currentWidget() is self.side.pages:
            self.side.pages.set_info(len(self.thumbs.selected_pids()), i, len(self.model.pages))
        self.stateChanged.emit()

    # ------------------------------------------------------------ листы

    def target_pids(self) -> list[str]:
        sel = self.thumbs.selected_pids()
        if sel:
            return sel
        i = self.view.current_page()
        return [self.model.pages[i]["id"]] if 0 <= i < len(self.model.pages) else []

    def rotate_pages(self, pids, delta: int = 90) -> None:
        if not pids:
            return
        self.model.rotate_pages(pids, delta)
        self.after_change(structure=True)

    def delete_pages(self, pids) -> None:
        if not pids:
            return
        if len(pids) >= len(self.model.pages):
            QMessageBox.information(self, "Удаление листов", "Нельзя удалить все листы документа.")
            return
        self.model.delete_pages(pids)
        self.thumbs.clearSelection()
        self.after_change(structure=True)
        self.toast(f"Удалено листов: {len(pids)}. Отменить — ⌘Z")

    def insert_blank(self, at: int | None = None) -> None:
        if at is None:
            at = self.view.current_page() + 1
        like = self.model.pages[max(0, min(at - 1, len(self.model.pages) - 1))]["id"] if self.model.pages else None
        pid = self.model.insert_blank(at, like_pid=like)
        self._register_sources()
        self.after_change(structure=True)
        self.view.go_to_page(self.model.page_index(pid))

    def insert_files_dialog(self, at: int | None = None) -> None:
        if at is None:
            at = self.view.current_page() + 1
        paths, _ = QFileDialog.getOpenFileNames(self, "Вставить PDF или картинки", self.ctx.last_dir(), OPEN_FILTER)
        if paths:
            self.ctx.set_last_dir(paths[0])
            self.insert_files(paths, at)

    def insert_files(self, paths: list[str], at: int) -> None:
        n = 0
        first = None
        for p in paths:
            if os.path.isdir(p):
                continue
            try:
                try:
                    sid = self.model.add_file_source(p)
                except NeedPassword:
                    pw = ask_password(self, os.path.basename(p))
                    if pw is None:
                        continue
                    sid = self.model.add_file_source(p, password=pw)
            except Exception as e:
                QMessageBox.warning(self, "Не получилось добавить файл", f"{os.path.basename(p)}:\n{e}")
                continue
            new = self.model.insert_source_pages(sid, at, commit=False)
            first = first or new[0]
            at += len(new)
            n += len(new)
        if n:
            self.model.commit()
            self._register_sources()
            self.after_change(structure=True)
            self.view.go_to_page(self.model.page_index(first))
            self.toast(f"Добавлено листов: {n}")

    def _on_thumb_order(self, order: list) -> None:
        if order != [p["id"] for p in self.model.pages]:
            self.model.set_order(order)
            self.after_change(structure=True)

    def _thumb_action(self, act: str, pids: list) -> None:
        if act == "rot":
            self.rotate_pages(pids, 90)
        elif act == "rotl":
            self.rotate_pages(pids, 270)
        elif act == "delete":
            self.delete_pages(pids)
        elif act == "extract":
            self.extract(pids)
        elif act == "blank_before":
            self.insert_blank(self.model.page_index(pids[0]))
        elif act == "blank_after":
            self.insert_blank(self.model.page_index(pids[-1]) + 1)
        elif act == "insert_after":
            self.insert_files_dialog(self.model.page_index(pids[-1]) + 1)

    def _page_action(self, act: str) -> None:
        pids = self.target_pids()
        if act == "rot":
            self.rotate_pages(pids, 90)
        elif act == "rotl":
            self.rotate_pages(pids, 270)
        elif act == "blank":
            self.insert_blank()
        elif act == "insert":
            self.insert_files_dialog()
        elif act == "extract":
            self.extract(pids)
        elif act == "split":
            self.split_doc()
        elif act == "delete":
            self.delete_pages(pids)

    # ------------------------------------------------------------ подписи и печати

    def refresh_library(self) -> None:
        items = self.library.items()
        if self._picked and not any(i["id"] == self._picked for i in items):
            self._picked = None

        def icon_for(it):
            pm = QPixmap(self.library.path(it["id"]))
            canvas = QPixmap(192, 144)
            canvas.fill(Qt.transparent)
            p = QPainter(canvas)
            p.setRenderHint(QPainter.SmoothPixmapTransform)
            if not pm.isNull():
                k = min(184 / pm.width(), 136 / pm.height())
                w, h = pm.width() * k, pm.height() * k
                p.drawPixmap(int((192 - w) / 2), int((144 - h) / 2), int(w), int(h), pm)
            p.end()
            return QIcon(canvas)

        self.side.stamps.set_items(items, icon_for, self._picked)

    def _pick_asset(self, iid) -> None:
        self._picked = iid
        if iid and self.mode != "stamp":
            self.ctx.request_mode("stamp")
        if iid:
            self.toast("Кликните на лист, куда поставить")

    def picked_asset(self):
        return self._picked

    def _remove_lib_item(self, iid: str) -> None:
        if QMessageBox.question(self, "Библиотека", "Убрать этот образец из библиотеки? В уже сохранённых "
                                "документах он останется.") == QMessageBox.Yes:
            self.library.remove(iid)
            self.ctx.libraryChanged.emit()

    def _asset_from_library(self, iid: str) -> str:
        png = self.library.png(iid)
        aid = self.model.add_asset(png)
        self.service.add_asset(aid, png)
        return aid

    def place_stamp(self, page, lp: QPointF) -> None:
        iid = self._picked
        it = next((i for i in self.library.items() if i["id"] == iid), None)
        if it is None:
            return
        aid = self._asset_from_library(iid)
        img = self.asset_image(aid)
        aspect = img.height() / max(1, img.width())
        w = it.get("width_pt")
        if not w:  # размер неизвестен: печать 40 мм по большей стороне, подпись 42 мм в ширину
            w = (113.4 if aspect <= 1 else 113.4 / aspect) if it.get("kind") == "stamp" else 120.0
        h = w * aspect
        cx, cy = page.to_unrot(lp)
        o = self.model.add_obj(page.pid, {"t": "image", "asset": aid, "cx": cx, "cy": cy, "w": w, "h": h,
                                          "angle": self.model.upright_angle(page.ref)})
        self.after_change(pages=[page.pid])
        self.view.select(o["id"])

    def stamp_to_pages(self, which: str) -> None:
        pid, o = self.view.selected_obj()
        if o is None or o["t"] != "image":
            return
        src_page = self.view.items_by_pid[pid]
        c = src_page.to_disp(o["cx"], o["cy"])
        fx, fy = c.x() / src_page.w, c.y() / src_page.h
        disp_ang = src_page.disp_angle(o.get("angle", 0))
        targets = [p["id"] for p in self.model.pages] if which == "all" else self.thumbs.selected_pids()
        if not targets:
            self.toast("Выберите листы в колонке миниатюр (⌘-клик), затем нажмите ещё раз")
            return
        n = 0
        for tp in targets:
            if tp == pid:
                continue
            page = self.view.items_by_pid[tp]
            cx, cy = page.to_unrot(QPointF(fx * page.w, fy * page.h))
            self.model.add_obj(tp, {"t": "image", "asset": o["asset"], "cx": cx, "cy": cy, "w": o["w"], "h": o["h"],
                                    "angle": (disp_ang - page.total_rot) % 360}, commit=False)
            n += 1
        self.model.commit()
        self.after_change(pages=targets)
        self.toast(f"Поставлено ещё на {n} лист(ов)")

    def rotate_selected_obj(self, deg: int) -> None:
        pid, o = self.view.selected_obj()
        if o is None or o["t"] != "image":
            return
        o["angle"] = (o.get("angle", 0) + deg) % 360
        self.model.commit()
        self.after_change(pages=[pid])

    def import_scan(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Скан подписи или печати", self.ctx.last_dir(),
                                              "Картинки и PDF (*.jpg *.jpeg *.png *.heic *.heif *.tif *.tiff *.pdf)")
        if not path:
            return
        self.ctx.set_last_dir(path)
        self.import_scan_file(path)

    def import_scan_file(self, path: str) -> None:
        try:
            data = open(path, "rb").read()
            dlg = StampImportDialog(data, os.path.basename(path), self)
        except Exception as e:
            QMessageBox.warning(self, "Не получилось открыть картинку", str(e))
            return
        if dlg.exec() and dlg.result is not None:
            png = images.to_png(dlg.result)
            it = self.library.add(png, dlg.kind(), name=os.path.splitext(os.path.basename(path))[0],
                                  width_pt=dlg.width_pt())
            self._picked = it["id"]
            self.ctx.libraryChanged.emit()
            self.ctx.request_mode("stamp")
            self.toast("Добавлено в библиотеку. Кликните на лист, куда поставить")

    def draw_signature(self) -> None:
        dlg = SignatureDialog(self)
        if dlg.exec():
            img = dlg.pad.image()
            if img is None:
                return
            # нарисованная подпись: 45 мм по ширине
            it = self.library.add(images.to_png(img), "signature", width_pt=127.0)
            self._picked = it["id"]
            self.ctx.libraryChanged.emit()
            self.ctx.request_mode("stamp")
            self.toast("Подпись добавлена. Кликните на лист, куда поставить")

    # ------------------------------------------------------------ поиск

    def open_search(self) -> None:
        self.searchbar.show()
        self.searchbar.edit.setFocus()
        self.searchbar.edit.selectAll()

    def close_search(self) -> None:
        self.searchbar.hide()
        self._search = {"query": "", "results": {}, "flat": [], "i": -1}
        self.view.show_search({}, None)
        self.view.setFocus()

    def search(self, query: str, keep_pos: bool = False) -> None:
        if not query:
            self.close_search()
            return
        prev = self._search.get("flat", [])[self._search.get("i", -1)] if keep_pos and self._search.get("flat") else None
        self._search["query"] = query
        items = []
        for ref in self.model.pages:
            objs = [o for o in self.model.page_objs(ref["id"]) if o.get("t") != "image"]
            items.append((ref["id"], ref["src"], ref["idx"], objs))
        self.searchbar.count.setText("ищу…")

        def done(res):
            results = {pid: quads for pid, quads in res[2]}
            flat = [(pid, i) for pid in [p["id"] for p in self.model.pages] for i in range(len(results.get(pid, [])))]
            cur = self.view.current_page()
            start = 0
            if prev in flat:
                start = flat.index(prev)
            else:
                for k, (pid, _) in enumerate(flat):
                    if self.model.page_index(pid) >= cur:
                        start = k
                        break
            self._search.update(results=results, flat=flat, i=start if flat else -1)
            self._show_search()

        def fail(msg):
            self.searchbar.count.setText("ошибка")

        sids = sorted({r["src"] for r in self.model.pages})
        self.service.submit("search", (items, query), sids=sids, priority=-3, key=("search", id(self)),
                            callback=done, errback=fail)

    def _show_search(self) -> None:
        s = self._search
        n = len(s["flat"])
        self.searchbar.count.setText(f"{s['i'] + 1} из {n}" if n else "не найдено")
        cur = s["flat"][s["i"]] if n else None
        self.view.show_search(s["results"], cur)

    def search_step(self, d: int) -> None:
        s = self._search
        if not s["flat"]:
            if self.searchbar.edit.text().strip():
                self.search(self.searchbar.edit.text().strip())
            return
        s["i"] = (s["i"] + d) % len(s["flat"])
        self._show_search()

    # ------------------------------------------------------------ сохранение

    def _confirm_restrictions(self) -> bool:
        if not self.model.main_source.restricted or self._restricted_ok:
            return True
        r = QMessageBox.question(self, "Файл защищён от изменений",
                                 "У файла есть ограничения на изменение (без пароля на открытие).\n"
                                 "Чтобы сохранить правки, программа снимет эти ограничения. Продолжить?",
                                 QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Yes)
        if r == QMessageBox.Yes:
            self._restricted_ok = True
            return True
        return False

    def save(self, done_cb=None) -> None:
        if self.view.editing is not None:
            self.view.finish_edit(True)
        if not self.model.path:
            self.save_as(done_cb)
            return
        if not self._confirm_restrictions():
            return
        self._save_to(self.model.path, done_cb=done_cb)

    def save_as(self, done_cb=None) -> None:
        if self.view.editing is not None:
            self.view.finish_edit(True)
        if not self._confirm_restrictions():
            return
        start = self.model.path or os.path.join(self.ctx.last_dir(), self.model.name)
        path, _ = QFileDialog.getSaveFileName(self, "Сохранить как", start, PDF_FILTER)
        if not path:
            return
        if not path.lower().endswith(".pdf"):
            path += ".pdf"
        self.ctx.set_last_dir(path)

        def after(ok, path=path):
            if ok:
                self.model.path = path
                self.model.name = os.path.basename(path)
                self.titleChanged.emit()
            if done_cb:
                done_cb(ok)

        self._save_to(path, done_cb=after, as_new=True)

    def _save_to(self, path: str, page_ids=None, done_cb=None, as_new=False, quiet=False, extra=None) -> None:
        if self._saving:
            return
        opts = {"compress": self.ctx.compress_mode(),
                "pristine": self.model.is_pristine() and not as_new and path == self.model.path}
        if extra:
            opts.update(extra)
            opts["compress"] = "none"
        if page_ids is not None:
            opts["pristine"] = False
        self._saving = True
        prog = QProgressDialog("Сохраняю…", None, 0, 0, self)
        prog.setWindowTitle("Сохранение")
        prog.setWindowModality(Qt.WindowModal)
        prog.setMinimumDuration(400)
        prog.setCancelButton(None)
        state = self.model.job_state()
        saved_index = self.model._hi
        aids = list(self.model.assets)

        def done(res):
            self._saving = False
            prog.close()
            if page_ids is None:
                if self.model._hi == saved_index:
                    self.model.mark_saved()
                else:  # пока сохраняли, успели изменить — помечаем нужную точку истории
                    self.model._saved_at = saved_index
                    self.titleChanged.emit()
            info = res[2]
            if not quiet:
                kb = info["size"] / 1024
                self.toast(f"Сохранено: {os.path.basename(path)} — {info['pages']} л., "
                           f"{kb / 1024:.1f} МБ" if kb > 1024 else f"Сохранено: {os.path.basename(path)} — "
                           f"{info['pages']} л., {kb:.0f} КБ")
            if done_cb:
                done_cb(True)

        def fail(msg):
            self._saving = False
            prog.close()
            QMessageBox.critical(self, "Не получилось сохранить",
                                 f"Файл не изменён.\n\n{msg}")
            if done_cb:
                done_cb(False)

        sids = sorted({r["src"] for r in self.model.pages} | {self.model.main})
        self.service.submit("save", (state, path, opts, page_ids), sids=sids, aids=aids, priority=-100,
                            callback=done, errback=fail)

    def optimize_dialog(self) -> None:
        from .optimize_dialog import OptimizeDialog, fmt_size
        if self.view.editing is not None:
            self.view.finish_edit(True)
        dlg = OptimizeDialog(self, self)
        if not dlg.exec():
            return
        base = os.path.splitext(self.model.name)[0]
        start = os.path.join(os.path.dirname(self.model.path or os.path.join(self.ctx.last_dir(), "x")),
                             f"{base}_оптимизированный.pdf")
        path, _ = QFileDialog.getSaveFileName(self, "Сохранить оптимизированный PDF", start, PDF_FILTER)
        if not path:
            return
        if not path.lower().endswith(".pdf"):
            path += ".pdf"
        before = len(self.model.main_source.data)

        def report(ok):
            if not ok:
                return
            after = os.path.getsize(path)
            d = 100 * (1 - after / max(1, before))
            QMessageBox.information(self, "Оптимизация", f"Готово: {os.path.basename(path)}\n\n"
                                    f"Было: {fmt_size(before)}\nСтало: {fmt_size(after)} "
                                    f"({'−' if d >= 0 else '+'}{abs(d):.0f} %)")

        self._save_to(path, done_cb=report, as_new=True, quiet=True, extra={"optimize": dlg.options()})

    def extract(self, pids) -> None:
        if not pids:
            return
        base = os.path.splitext(self.model.name)[0]
        idxs = sorted(self.model.page_index(p) for p in pids)
        name = ops.part_name(base, idxs) if idxs == list(range(idxs[0], idxs[-1] + 1)) else f"{base}_листы.pdf"
        start = os.path.join(os.path.dirname(self.model.path or os.path.join(self.ctx.last_dir(), "x")), name)
        path, _ = QFileDialog.getSaveFileName(self, f"Извлечь листы ({len(pids)})", start, PDF_FILTER)
        if not path:
            return
        if not path.lower().endswith(".pdf"):
            path += ".pdf"
        ordered = [p["id"] for p in self.model.pages if p["id"] in set(pids)]
        self._save_to(path, page_ids=ordered)

    def split_doc(self) -> None:
        dlg = SplitDialog(len(self.model.pages), self)
        if not dlg.exec():
            return
        folder = QFileDialog.getExistingDirectory(self, "Куда сохранить части",
                                                  os.path.dirname(self.model.path or "") or self.ctx.last_dir())
        if not folder:
            return
        base = os.path.splitext(self.model.name)[0]
        parts = dlg.parts
        existing = [ops.part_name(base, p) for p in parts if os.path.exists(os.path.join(folder, ops.part_name(base, p)))]
        if existing and QMessageBox.question(self, "Разделить", f"Файлы уже существуют ({len(existing)}), "
                                             "заменить?") != QMessageBox.Yes:
            return
        left = [len(parts)]

        def one_done(ok):
            left[0] -= 1
            if left[0] == 0:
                self.toast(f"Готово: {len(parts)} файл(ов) в папке «{os.path.basename(folder)}»")

        def run(k=0):
            if k >= len(parts):
                return
            p = parts[k]
            self._save_to(os.path.join(folder, ops.part_name(base, p)),
                          page_ids=[self.model.pages[i]["id"] for i in p],
                          done_cb=lambda ok, k=k: (one_done(ok), run(k + 1)), quiet=True)

        run(0)

    def can_close(self) -> bool:
        if self.view.editing is not None:
            self.view.finish_edit(True)
        if not self.model.dirty:
            return True
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setText(f"Сохранить изменения в «{self.model.name}»?")
        box.setInformativeText("Если не сохранить, изменения пропадут.")
        sb = box.addButton("Сохранить", QMessageBox.AcceptRole)
        db = box.addButton("Не сохранять", QMessageBox.DestructiveRole)
        box.addButton("Отмена", QMessageBox.RejectRole)
        box.setDefaultButton(sb)
        box.exec()
        b = box.clickedButton()
        if b is db:
            return True
        if b is sb:
            result = []
            loop_done = []
            from PySide6.QtCore import QEventLoop
            loop = QEventLoop()

            def cb(ok):
                result.append(ok)
                loop_done.append(1)
                loop.quit()

            self.save(done_cb=cb)
            if not loop_done and self._saving:
                loop.exec()
            elif not loop_done:
                return False  # отменили диалог «Сохранить как»
            return bool(result and result[0])
        return False
