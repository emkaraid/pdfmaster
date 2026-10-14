"""«Проверить обновления»: только по кнопке, один запрос к GitHub.

Программа сама в сеть не ходит. По нажатию она спрашивает у GitHub последний
выпуск, и если он новее — скачивает установщик для этой системы в «Загрузки»
и открывает его.
"""
from __future__ import annotations

import json
import os
import ssl
import subprocess
import sys
import threading
import urllib.request

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import QApplication, QMessageBox, QProgressDialog

from ..version import parse, version

REPO = "emkaraid/pdfmaster"
API = f"https://api.github.com/repos/{REPO}/releases/latest"
PAGE = f"https://github.com/{REPO}/releases/latest"


def _ctx():
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def _asset_suffix() -> str:
    return "-windows-setup.exe" if sys.platform.startswith("win") else "-mac.dmg"


def fetch_latest(timeout: float = 10.0) -> dict:
    """Последний выпуск. Версию берём по переадресации страницы «latest» (у неё нет лимита
    запросов, как у API GitHub), установщик — по постоянной ссылке latest/download."""
    req = urllib.request.Request(PAGE, method="HEAD", headers={"User-Agent": "PDFMaster"})
    with urllib.request.urlopen(req, timeout=timeout, context=_ctx()) as r:
        final = r.geturl()                      # …/releases/tag/v1.2.0
    tag = final.rstrip("/").rsplit("/", 1)[-1]
    if "/tag/" not in final:
        raise RuntimeError("выпусков пока нет")
    name = "PDFMaster" + _asset_suffix()
    notes = ""
    try:  # описание выпуска — по возможности (у API есть лимит, без него обойдёмся)
        api = urllib.request.Request(API, headers={"Accept": "application/vnd.github+json",
                                                   "User-Agent": "PDFMaster"})
        with urllib.request.urlopen(api, timeout=timeout, context=_ctx()) as r:
            notes = json.loads(r.read().decode("utf-8")).get("body", "") or ""
    except Exception:
        pass
    return {"version": tag.lstrip("vV"), "notes": notes,
            "url": f"https://github.com/{REPO}/releases/latest/download/{name}", "name": name, "size": 0}


class _Bridge(QObject):
    done = Signal(object, object)       # результат, ошибка
    progress = Signal(int, int)


class Updater:
    def __init__(self, parent):
        self.parent = parent
        self.bridge = _Bridge()
        self.bridge.done.connect(self._checked)

    def check(self) -> None:
        self.bridge.done.disconnect()
        self.bridge.done.connect(self._checked)
        QApplication.setOverrideCursor(Qt.WaitCursor)

        def work():
            try:
                self.bridge.done.emit(fetch_latest(), None)
            except Exception as e:  # нет сети, GitHub недоступен
                self.bridge.done.emit(None, e)

        threading.Thread(target=work, daemon=True).start()

    def _checked(self, info, err) -> None:
        QApplication.restoreOverrideCursor()
        cur = version()
        if err is not None:
            QMessageBox.warning(self.parent, "Обновления",
                                f"Не удалось связаться с GitHub. Проверьте интернет.\n\n{err}")
            return
        if not info["version"] or parse(info["version"]) <= parse(cur):
            QMessageBox.information(self.parent, "Обновления", f"У вас последняя версия ({cur}).")
            return
        if not info["url"]:
            QMessageBox.information(self.parent, "Обновления",
                                    f"Есть версия {info['version']}, но установщика для этой системы в ней нет.\n"
                                    f"Страница: {PAGE}")
            return
        box = QMessageBox(self.parent)
        box.setWindowTitle("Обновления")
        box.setText(f"Доступна новая версия {info['version']} (у вас {cur}).")
        notes = info["notes"].strip()
        if notes:
            box.setDetailedText(notes)
        box.setInformativeText("Скачать установщик и открыть его? Документы перед этим лучше сохранить.")
        ok = box.addButton("Скачать и установить", QMessageBox.AcceptRole)
        box.addButton("Позже", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is ok:
            self._download(info)

    def _download(self, info) -> None:
        dest_dir = os.path.join(os.path.expanduser("~"), "Downloads")
        os.makedirs(dest_dir, exist_ok=True)
        dest = os.path.join(dest_dir, info["name"])
        prog = QProgressDialog("Скачиваю обновление…", "Отмена", 0, max(1, info["size"]), self.parent)
        prog.setWindowTitle("Обновление")
        prog.setWindowModality(Qt.WindowModal)
        prog.setMinimumDuration(0)
        cancel = {"v": False}
        prog.canceled.connect(lambda: cancel.__setitem__("v", True))
        self.bridge.progress.connect(lambda a, b: (prog.setMaximum(max(1, b)), prog.setValue(a)))
        self.bridge.done.disconnect()
        self.bridge.done.connect(lambda path, err: self._downloaded(prog, path, err))

        def work():
            try:
                req = urllib.request.Request(info["url"], headers={"User-Agent": "PDFMaster"})
                tmp = dest + ".part"
                with urllib.request.urlopen(req, timeout=30, context=_ctx()) as r, open(tmp, "wb") as f:
                    total = int(r.headers.get("Content-Length") or info["size"] or 0)
                    got = 0
                    while True:
                        if cancel["v"]:
                            raise RuntimeError("отменено")
                        chunk = r.read(256 * 1024)
                        if not chunk:
                            break
                        f.write(chunk)
                        got += len(chunk)
                        self.bridge.progress.emit(got, total)
                os.replace(tmp, dest)
                self.bridge.done.emit(dest, None)
            except Exception as e:
                try:
                    os.unlink(dest + ".part")
                except OSError:
                    pass
                self.bridge.done.emit(None, e)

        threading.Thread(target=work, daemon=True).start()

    def _downloaded(self, prog, path, err) -> None:
        prog.close()
        if err is not None:
            if str(err) != "отменено":
                QMessageBox.warning(self.parent, "Обновление", f"Не удалось скачать: {err}")
            return
        if sys.platform.startswith("win"):
            QMessageBox.information(self.parent, "Обновление",
                                    "Установщик скачан. Программа закроется, и откроется установка новой версии.")
            os.startfile(path)  # noqa: S606 — запуск скачанного установщика по согласию пользователя
            self.parent.close()
        else:
            subprocess.Popen(["open", path])
            QMessageBox.information(self.parent, "Обновление",
                                    "Образ с новой версией открыт. Закройте программу и перетащите "
                                    "«PDFMaster» в «Программы» с заменой.")
