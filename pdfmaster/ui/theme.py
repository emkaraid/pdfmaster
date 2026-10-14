"""Оформление: цветовые токены (светлая и тёмная тема) и общий стиль приложения.

Один источник цветов на всю программу. Фирменный цвет — красно-оранжевый из иконки
(#D6452F): им отмечены активный режим, главные кнопки и выбранные листы. Синий
остаётся только за выделением текста на листе — как в любом редакторе.
"""
from __future__ import annotations

import os
import tempfile

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPalette, QPen, QPixmap

LIGHT = {
    "bg": "#F3F4F7",          # фон окна, полосы вкладок
    "surface": "#FFFFFF",     # панели, карточки
    "surface2": "#F8F9FB",    # колонка миниатюр, поля
    "border": "#E3E6EB",
    "border2": "#D2D7DF",
    "text": "#1D2330",
    "text2": "#5B6575",
    "text3": "#8C95A4",
    "accent": "#D6452F",
    "accent_hover": "#C03A25",
    "accent_text": "#B8361F",
    "accent_tint": "rgba(214, 69, 47, 0.11)",
    "accent_tint2": "rgba(214, 69, 47, 0.18)",
    "hover": "rgba(29, 35, 48, 0.06)",
    "pressed": "rgba(29, 35, 48, 0.11)",
    "canvas": "#E8EAEE",
    "select": "#2F6FD6",
    "scroll": "rgba(29, 35, 48, 0.22)",
    "scroll_hover": "rgba(29, 35, 48, 0.38)",
    "tip_bg": "#1D2330",
    "tip_fg": "#FFFFFF",
}

DARK = {
    "bg": "#1C1D21",
    "surface": "#25272C",
    "surface2": "#202226",
    "border": "#34373E",
    "border2": "#43474F",
    "text": "#E9EBEF",
    "text2": "#A8AFBB",
    "text3": "#7A8290",
    "accent": "#EE6A50",
    "accent_hover": "#F27E67",
    "accent_text": "#FF8F77",
    "accent_tint": "rgba(238, 106, 80, 0.16)",
    "accent_tint2": "rgba(238, 106, 80, 0.26)",
    "hover": "rgba(255, 255, 255, 0.06)",
    "pressed": "rgba(255, 255, 255, 0.11)",
    "canvas": "#16171A",
    "select": "#5B8FE8",
    "scroll": "rgba(255, 255, 255, 0.20)",
    "scroll_hover": "rgba(255, 255, 255, 0.34)",
    "tip_bg": "#E9EBEF",
    "tip_fg": "#1D2330",
}


def is_dark() -> bool:
    return QGuiApplication.palette().color(QPalette.Window).lightness() < 128


def tokens() -> dict:
    return DARK if is_dark() else LIGHT


def color(name: str) -> QColor:
    v = tokens()[name]
    if v.startswith("rgba"):
        r, g, b, a = (x.strip() for x in v[5:-1].split(","))
        return QColor(int(r), int(g), int(b), int(float(a) * 255))
    return QColor(v)


def _close_png(c: str, size: int = 16) -> str:
    """Крестик закрытия вкладки нужного цвета — файлом, иначе QSS его не возьмёт."""
    d = os.path.join(tempfile.gettempdir(), "pdfmaster-theme")
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, f"close-{c.lstrip('#')}-{size}.png")
    if not os.path.exists(path):
        pm = QPixmap(size * 2, size * 2)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(QColor(c), 2.4, Qt.SolidLine, Qt.RoundCap))
        a, b = size * 0.62, size * 1.38
        p.drawLine(QPointF(a, a), QPointF(b, b))
        p.drawLine(QPointF(b, a), QPointF(a, b))
        p.end()
        pm.save(path)
    return path.replace("\\", "/")


def _chevron_png(c: str, up: bool, size: int = 10) -> str:
    """Стрелка для счётчиков и выпадающих списков (файлом — для QSS)."""
    d = os.path.join(tempfile.gettempdir(), "pdfmaster-theme")
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, f"chev-{'u' if up else 'd'}-{c.lstrip('#')}-{size}.png")
    if not os.path.exists(path):
        pm = QPixmap(size * 2, size * 2)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(QColor(c), 2.2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        a, b, m = size * 0.45, size * 1.55, size
        y0, y1 = (size * 1.3, size * 0.7) if up else (size * 0.7, size * 1.3)
        p.drawLine(QPointF(a, y0), QPointF(m, y1))
        p.drawLine(QPointF(m, y1), QPointF(b, y0))
        p.end()
        pm.save(path)
    return path.replace("\\", "/")


def stylesheet() -> str:
    t = tokens()
    up, down = _chevron_png(t["text2"], True), _chevron_png(t["text2"], False)
    return f"""
QMainWindow, QStackedWidget#rootStack, QWidget#welcome {{ background: {t['bg']}; }}

/* ---------- панель инструментов ---------- */
QToolBar#main {{
    background: {t['surface']}; border: none; border-bottom: 1px solid {t['border']};
    padding: 5px 10px; spacing: 2px;
}}
QToolBar#main QToolButton {{
    border: none; border-radius: 8px; padding: 4px 7px; margin: 0 1px;
    color: {t['text2']}; font-size: 12px;
}}
QToolBar#main QToolButton:hover {{ background: {t['hover']}; color: {t['text']}; }}
QToolBar#main QToolButton:pressed {{ background: {t['pressed']}; }}
QToolBar#main QToolButton:checked {{ background: {t['accent_tint']}; color: {t['accent_text']}; }}
QToolBar#main QToolButton:checked:hover {{ background: {t['accent_tint2']}; }}
QToolBar#main QToolButton:disabled {{ color: {t['text3']}; }}
QToolBar#main QToolButton#zoomLbl {{ color: {t['text']}; font-weight: 600; min-width: 50px; }}
QToolBar::separator {{ background: {t['border']}; width: 1px; margin: 8px 7px; }}
QFrame#pill {{ background: {t['surface2']}; border: 1px solid {t['border']}; border-radius: 9px; }}
QFrame#pill QToolButton {{ border: none; border-radius: 7px; padding: 3px 5px; color: {t['text2']}; }}
QFrame#pill QToolButton:hover {{ background: {t['hover']}; color: {t['text']}; }}
QFrame#pill QToolButton:pressed {{ background: {t['pressed']}; }}

/* ---------- вкладки документов ---------- */
QTabWidget::pane {{ border: none; }}
QTabWidget#docTabs QTabBar {{ background: {t['bg']}; }}
QTabWidget#docTabs QTabBar::tab {{
    background: transparent; color: {t['text2']};
    padding: 6px 10px 6px 14px; margin: 6px 0 0 8px; min-width: 120px; max-width: 320px;
    border: 1px solid transparent; border-bottom: none;
    border-top-left-radius: 9px; border-top-right-radius: 9px;
}}
QTabWidget#docTabs QTabBar::tab:selected {{
    background: {t['surface']}; color: {t['text']}; border-color: {t['border']};
}}
QTabWidget#docTabs QTabBar::tab:hover:!selected {{ background: {t['hover']}; color: {t['text']}; }}
QToolButton#tabClose {{ border: none; border-radius: 6px; padding: 0; margin: 0 0 0 4px; }}
QToolButton#tabClose:hover {{ background: {t['pressed']}; }}

/* ---------- кнопки и поля ---------- */
QPushButton {{
    background: {t['surface']}; color: {t['text']};
    border: 1px solid {t['border2']}; border-radius: 8px; padding: 6px 12px;
}}
QPushButton:hover {{ background: {t['surface2']}; border-color: {t['text3']}; }}
QPushButton:pressed {{ background: {t['pressed']}; }}
QPushButton:disabled {{ color: {t['text3']}; border-color: {t['border']}; }}
QPushButton[primary="true"] {{
    background: {t['accent']}; border-color: {t['accent']}; color: white; font-weight: 600; padding: 8px 18px;
}}
QPushButton[primary="true"]:hover {{ background: {t['accent_hover']}; border-color: {t['accent_hover']}; }}
QPushButton[ghost="true"] {{ background: transparent; border-color: transparent; color: {t['text2']}; }}
QPushButton[ghost="true"]:hover {{ background: {t['hover']}; color: {t['text']}; }}
QPushButton[listitem="true"] {{
    text-align: left; padding: 7px 10px; background: transparent;
    border: 1px solid transparent; border-radius: 8px; color: {t['text']};
}}
QPushButton[listitem="true"]:hover {{ background: {t['hover']}; }}
QPushButton[listitem="true"]:pressed {{ background: {t['pressed']}; }}
QPushButton[danger="true"] {{ color: #C0262D; }}

QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background: {t['surface']}; color: {t['text']};
    border: 1px solid {t['border2']}; border-radius: 7px; padding: 4px 7px;
    selection-background-color: {t['select']}; selection-color: white;
}}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{ border-color: {t['accent']}; }}
QAbstractSpinBox QLineEdit, QComboBox QLineEdit {{ border: none; padding: 0; margin: 0; background: transparent; }}
QComboBox::drop-down {{ border: none; width: 20px; }}
QComboBox::down-arrow {{ image: url({down}); width: 10px; height: 10px; }}
QAbstractSpinBox {{ padding-right: 18px; }}
QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {{
    subcontrol-origin: border; width: 18px; border: none; background: transparent;
}}
QAbstractSpinBox::up-button {{ subcontrol-position: top right; border-top-right-radius: 7px; }}
QAbstractSpinBox::down-button {{ subcontrol-position: bottom right; border-bottom-right-radius: 7px; }}
QAbstractSpinBox::up-button:hover, QAbstractSpinBox::down-button:hover {{ background: {t['hover']}; }}
QAbstractSpinBox::up-arrow {{ image: url({up}); width: 9px; height: 9px; }}
QAbstractSpinBox::down-arrow {{ image: url({down}); width: 9px; height: 9px; }}
QToolButton[chip="true"] {{
    border: 1px solid {t['border2']}; border-radius: 7px; background: {t['surface']};
    color: {t['text2']}; padding: 3px 9px;
}}
QToolButton[chip="true"]:hover {{ background: {t['hover']}; color: {t['text']}; }}
QToolButton[toggle="true"] {{
    border: 1px solid {t['border2']}; border-radius: 7px; background: {t['surface']};
    color: {t['text']}; font-size: 14px; min-width: 30px; min-height: 26px;
}}
QToolButton[toggle="true"]:hover {{ background: {t['hover']}; }}
QToolButton[toggle="true"]:checked {{
    background: {t['accent_tint2']}; border-color: {t['accent']}; color: {t['accent_text']};
}}
QListWidget#stampLib {{ background: {t['surface2']}; border: 1px solid {t['border']}; border-radius: 10px;
    padding: 6px; outline: none; }}
QListWidget#stampLib::item {{ border: 2px solid transparent; border-radius: 10px; padding: 4px;
    color: {t['text2']}; }}
QListWidget#stampLib::item:hover {{ background: {t['hover']}; }}
QListWidget#stampLib::item:selected {{ background: {t['accent_tint']}; border-color: {t['accent']};
    color: {t['text']}; }}
QComboBox QAbstractItemView {{
    background: {t['surface']}; color: {t['text']}; border: 1px solid {t['border']};
    selection-background-color: {t['accent_tint2']}; selection-color: {t['text']}; outline: none;
}}

/* ---------- панели ---------- */
QScrollArea#sidePanel, QWidget#propsPanel {{ background: {t['surface']}; border: none; }}
QScrollArea#sidePanel {{ border-left: 1px solid {t['border']}; }}
QLabel[role="title"] {{ color: {t['text']}; font-size: 15px; font-weight: 700; }}
QLabel[role="section"] {{ color: {t['text3']}; font-size: 11px; font-weight: 600; letter-spacing: 0.5px; }}
QLabel[role="hint"] {{ color: {t['text2']}; font-size: 11px; }}
QFrame#hintBox {{ background: {t['surface2']}; border: 1px solid {t['border']}; border-radius: 9px; }}
QListWidget#thumbs {{ background: {t['surface2']}; border: none; border-right: 1px solid {t['border']}; outline: none; }}
QSplitter::handle {{ background: {t['border']}; }}

/* ---------- строка состояния ---------- */
QStatusBar {{ background: {t['surface']}; border-top: 1px solid {t['border']}; color: {t['text2']}; }}
QStatusBar::item {{ border: none; }}
QStatusBar QLabel {{ color: {t['text2']}; }}
QStatusBar QLabel#modeHint {{ color: {t['text2']}; padding-left: 6px; }}
QStatusBar QLabel#modeName {{
    color: {t['accent_text']}; background: {t['accent_tint']}; border-radius: 6px;
    padding: 1px 8px; font-weight: 600;
}}
QStatusBar QToolButton {{ border: none; border-radius: 6px; padding: 2px; }}
QStatusBar QSpinBox#pageSpin {{ padding: 0 4px; font-weight: 600; }}
QSpinBox#pageSpin {{ padding-right: 4px; }}
QSpinBox#pageSpin::up-button, QSpinBox#pageSpin::down-button {{ width: 0; border: none; }}
QSpinBox#pageSpin::up-arrow, QSpinBox#pageSpin::down-arrow {{ image: none; width: 0; height: 0; }}
QToolBar#main QToolButton::menu-button {{ border: none; width: 14px; border-radius: 6px; }}
QToolBar#main QToolButton::menu-button:hover {{ background: {t['hover']}; }}
QStatusBar QToolButton:hover {{ background: {t['hover']}; }}

/* ---------- стартовый экран ---------- */
QFrame#welcomeCard {{ background: {t['surface']}; border: 1px solid {t['border']}; border-radius: 18px; }}
QLabel#welcomeTitle {{ color: {t['text']}; font-size: 26px; font-weight: 700; }}
QLabel#welcomeSub {{ color: {t['text2']}; font-size: 13px; }}
QFrame#dropZone {{
    background: {t['surface2']}; border: 2px dashed {t['border2']}; border-radius: 14px;
}}
QFrame#dropZone[active="true"] {{ background: {t['accent_tint']}; border-color: {t['accent']}; }}
QLabel#dropTitle {{ color: {t['text']}; font-size: 15px; font-weight: 600; }}
QLabel#dropSub {{ color: {t['text3']}; font-size: 12px; }}
QPushButton[recent="true"] {{
    text-align: left; padding: 8px 12px; background: transparent; color: {t['text']};
    border: 1px solid transparent; border-radius: 10px;
}}
QPushButton[recent="true"]:hover {{ background: {t['hover']}; border-color: {t['border']}; }}

/* ---------- прокрутка, подсказки, меню ---------- */
QScrollBar:vertical {{ background: transparent; width: 11px; margin: 2px; }}
QScrollBar:horizontal {{ background: transparent; height: 11px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {t['scroll']}; border-radius: 3px; min-height: 36px; margin: 0 2px; }}
QScrollBar::handle:horizontal {{ background: {t['scroll']}; border-radius: 3px; min-width: 36px; margin: 2px 0; }}
QScrollBar::handle:hover {{ background: {t['scroll_hover']}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QToolTip {{
    background: {t['tip_bg']}; color: {t['tip_fg']}; border: none; border-radius: 6px; padding: 5px 8px;
}}
QMenu {{
    background: {t['surface']}; color: {t['text']}; border: 1px solid {t['border']};
    border-radius: 10px; padding: 5px;
}}
QMenu::item {{ padding: 6px 22px 6px 12px; border-radius: 6px; }}
QMenu::item:selected {{ background: {t['accent_tint2']}; color: {t['text']}; }}
QMenu::separator {{ height: 1px; background: {t['border']}; margin: 4px 8px; }}
"""


def apply(app) -> None:
    app.setStyleSheet(stylesheet())
