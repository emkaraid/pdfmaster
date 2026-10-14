"""Точка входа: python -m pdfmaster [файлы…]"""
from __future__ import annotations

import multiprocessing
import os
import sys


def main(argv: list[str] | None = None) -> int:
    multiprocessing.freeze_support()
    try:
        multiprocessing.set_start_method("spawn")
    except RuntimeError:
        pass
    from PySide6.QtCore import QEvent, Qt, QTimer
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication

    from .core.fonts import resource_dir
    from .ui.main_window import APP_TITLE, AppContext, MainWindow

    class App(QApplication):
        def __init__(self, args):
            super().__init__(args)
            self.window = None
            self.pending: list[str] = []

        def event(self, e):
            if e.type() == QEvent.FileOpen:  # двойной клик по PDF в Finder, «Открыть с помощью»
                path = e.file()
                if path:
                    if self.window is not None:
                        self.window.open_paths([path])
                    else:
                        self.pending.append(path)
                return True
            if e.type() == QEvent.Quit and self.window is not None:
                # ⌘Q, «Завершить» в Dock, выход из системы — окно должно закрыться по-настоящему
                self.window.quitting = True
                try:
                    return super().event(e)
                finally:  # если выход отменили («Отмена» в вопросе о сохранении)
                    self.window.quitting = False
            return super().event(e)

    argv = list(sys.argv if argv is None else argv)
    if sys.platform.startswith("win"):
        try:  # своя иконка на панели задач, а не иконка python.exe
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("PDFMaster.PDFMasterskaya")
        except Exception:
            pass
    app = App(argv)
    from .ui import platform_text
    platform_text.install(app)
    from PySide6.QtCore import QLibraryInfo, QLocale, QTranslator
    QLocale.setDefault(QLocale(QLocale.Russian, QLocale.Russia))
    tr = QTranslator(app)
    if tr.load("qtbase_ru", resource_dir()) or tr.load("qtbase_ru", QLibraryInfo.path(QLibraryInfo.TranslationsPath)):
        app.installTranslator(tr)
    app.setApplicationName(APP_TITLE)
    app.setApplicationDisplayName(APP_TITLE)
    app.setOrganizationName("PDFMaster")
    icon_path = os.path.join(resource_dir(), "icon.png")
    if os.path.exists(icon_path):
        app.setWindowIcon(QIcon(icon_path))
    ctx = AppContext()
    win = MainWindow(ctx)
    app.window = win
    if platform_text.IS_WIN:
        from PySide6.QtGui import QAction
        for a in win.findChildren(QAction):
            platform_text.fix_action(a)
    win.show()
    if sys.platform == "darwin":
        # закрытое окно не завершает программу; щелчок по значку в Dock открывает его снова
        app.setQuitOnLastWindowClosed(False)

        def reopen(state):
            if state == Qt.ApplicationActive and not win.isVisible():
                win.show()
                win.raise_()
        app.applicationStateChanged.connect(reopen)
    files = [a for a in argv[1:] if not a.startswith("-") and os.path.exists(a)]
    QTimer.singleShot(0, lambda: win.open_paths(files + app.pending) if files or app.pending else None)
    app.aboutToQuit.connect(ctx.service.shutdown)
    script = os.environ.get("PDFM_SELFTEST_SCRIPT")
    if script:  # прогон сценария внутри собранного приложения (проверка сборки)
        def run_script():
            g = {"app": app, "win": win, "ctx": ctx, "__name__": "__selftest__", "__file__": script}
            try:
                exec(compile(open(script, encoding="utf-8").read(), script, "exec"), g)
            except BaseException:
                import traceback
                traceback.print_exc()
            finally:
                for i in range(win.tabs.count()):
                    win.tabs.widget(i).model.mark_saved()
                app.quit()
        QTimer.singleShot(300, run_script)
    shot = os.environ.get("PDFM_SELFTEST_SHOT")
    if shot:  # самопроверка сборки: дождаться отрисовки, сохранить снимок окна и выйти
        def check():
            t = win.tab()
            ready = t is not None and all(it.base is not None for it, _ in t.view.visible_pages(0))
            if ready or check.n > 150:
                win.grab().save(shot)
                with open(shot + ".txt", "w") as f:
                    f.write(f"ready={ready} tabs={win.tabs.count()} pages={len(t.model.pages) if t else 0}\n")
                app.quit()
                return
            check.n += 1
            QTimer.singleShot(100, check)
        check.n = 0
        QTimer.singleShot(500, check)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
