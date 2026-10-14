"""Тексты интерфейса под Windows: ⌘ → Ctrl+, ⇧ → Shift+, Finder → Проводник.

Исходные строки написаны для Mac. На Windows фильтр событий приложения
поправляет текст и подсказки каждого виджета перед первым показом, а
сеттеры setText/setToolTip пропускают новые строки через kb().
"""
from __future__ import annotations

import sys

from PySide6.QtCore import QEvent, QObject

IS_WIN = sys.platform.startswith("win")
IS_MAC = sys.platform == "darwin"

_RULES = [
    ("⇧⌘", "Ctrl+Shift+"), ("⌥⌘", "Ctrl+Alt+"), ("⌘", "Ctrl+"), ("⇧", "Shift+"), ("⌥", "Alt+"),
    ("⌫", "Del"), ("из Finder", "из Проводника"), ("Finder", "Проводник"),
    ("на этом Mac", "на этом компьютере"), ("этом Mac", "этом компьютере"),
]


def kb(text):
    """Текст с обозначениями клавиш для текущей системы."""
    if not IS_WIN or not isinstance(text, str) or not text:
        return text
    for a, b in _RULES:
        if a in text:
            text = text.replace(a, b)
    return text


class _Localizer(QObject):
    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.Polish:
            _fix(obj)
        return False


def _fix(w) -> None:
    for get, set_ in (("text", "setText"), ("toolTip", "setToolTip"), ("placeholderText", "setPlaceholderText"),
                      ("windowTitle", "setWindowTitle")):
        g = getattr(w, get, None)
        s = getattr(w, set_, None)
        if callable(g) and callable(s):
            try:
                v = g()
            except Exception:
                continue
            nv = kb(v)
            if isinstance(v, str) and nv != v:
                s(nv)
    acts = getattr(w, "actions", None)
    if callable(acts):
        try:
            for a in acts():
                fix_action(a)
        except Exception:
            pass


def fix_action(a) -> None:
    for get, set_ in (("text", "setText"), ("toolTip", "setToolTip"), ("iconText", "setIconText")):
        v = getattr(a, get)()
        nv = kb(v)
        if nv != v:
            getattr(a, set_)(nv)


def install(app) -> None:
    """Включает замену обозначений клавиш (только на Windows)."""
    if not IS_WIN:
        return
    from PySide6.QtGui import QAction
    from PySide6.QtWidgets import QAbstractButton, QLabel, QLineEdit, QWidget
    for cls in (QLabel, QAbstractButton):
        orig = cls.setText
        cls.setText = (lambda o: (lambda self, t: o(self, kb(t))))(orig)
    for cls, name in ((QWidget, "setToolTip"), (QAction, "setToolTip"), (QAction, "setText"),
                      (QLineEdit, "setPlaceholderText")):
        orig = getattr(cls, name)
        setattr(cls, name, (lambda o: (lambda self, t: o(self, kb(t))))(orig))
    app._pdfm_localizer = _Localizer(app)
    app.installEventFilter(app._pdfm_localizer)
