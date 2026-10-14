"""Диалог «Оптимизировать PDF» — как «Оптимизатор PDF» в Acrobat."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QGroupBox, QHBoxLayout,
                               QLabel, QSlider, QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout, QHeaderView)

from ..core import optimize as O


def fmt_size(n: int) -> str:
    if n >= 1024 * 1024:
        return f"{n / 1024 / 1024:.1f} МБ"
    return f"{max(1, round(n / 1024))} КБ"


class OptimizeDialog(QDialog):
    def __init__(self, tab, parent=None):
        super().__init__(parent)
        self.tab = tab
        self.setWindowTitle("Оптимизировать PDF")
        lay = QHBoxLayout(self)
        # --- аудит
        left = QVBoxLayout()
        left.addWidget(QLabel("<b>Что занимает место</b>"))
        self.audit = QTableWidget(0, 3)
        self.audit.setHorizontalHeaderLabels(["Категория", "Размер", "%"])
        self.audit.verticalHeader().setVisible(False)
        self.audit.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.audit.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.audit.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.audit.setEditTriggers(QTableWidget.NoEditTriggers)
        self.audit.setSelectionMode(QTableWidget.NoSelection)
        self.audit.setMinimumWidth(400)
        left.addWidget(self.audit, 1)
        self.total = QLabel("Считаю…")
        left.addWidget(self.total)
        lay.addLayout(left, 1)
        # --- настройки
        right = QVBoxLayout()
        row = QHBoxLayout()
        row.addWidget(QLabel("Настройка:"))
        self.preset = QComboBox()
        for k, v in O.PRESETS.items():
            self.preset.addItem(v["label"], k)
        self.preset.addItem("Своя", "custom")
        self.preset.setCurrentIndex(1)
        row.addWidget(self.preset, 1)
        right.addLayout(row)

        g = QGroupBox("Картинки")
        f = QFormLayout(g)
        self.images = QCheckBox("Пересжимать картинки и сканы")
        self.images.setChecked(True)
        f.addRow(self.images)
        self.dpi = QSpinBox()
        self.dpi.setRange(50, 1200)
        self.dpi.setSuffix(" dpi")
        self.thr = QSpinBox()
        self.thr.setRange(60, 2400)
        self.thr.setSuffix(" dpi")
        f.addRow("Снижать разрешение до", self.dpi)
        f.addRow("…если оно больше", self.thr)
        qrow = QHBoxLayout()
        self.quality = QSlider(Qt.Horizontal)
        self.quality.setRange(20, 100)
        self.qlbl = QLabel()
        self.qlbl.setMinimumWidth(110)
        qrow.addWidget(self.quality, 1)
        qrow.addWidget(self.qlbl)
        f.addRow("Качество JPEG", qrow)
        self.bitonal = QCheckBox("Чёрно-белые сканы — тоже")
        self.bitonal.setChecked(True)
        f.addRow(self.bitonal)
        self.gray = QCheckBox("Перевести весь документ в оттенки серого")
        f.addRow(self.gray)
        right.addWidget(g)

        g2 = QGroupBox("Шрифты")
        v2 = QVBoxLayout(g2)
        self.subset = QCheckBox("Оставить во встроенных шрифтах только используемые символы")
        self.subset.setChecked(True)
        v2.addWidget(self.subset)
        right.addWidget(g2)

        g3 = QGroupBox("Удалить")
        v3 = QVBoxLayout(g3)
        self.js = QCheckBox("JavaScript и действия")
        self.js.setChecked(True)
        self.thumbs = QCheckBox("Встроенные миниатюры листов")
        self.thumbs.setChecked(True)
        self.meta = QCheckBox("Метаданные и сведения о документе (автор, программа)")
        self.attach = QCheckBox("Вложенные файлы")
        self.annots = QCheckBox("Комментарии и пометки (аннотации)")
        self.links = QCheckBox("Ссылки")
        self.flatten = QCheckBox("Впечатать комментарии и поля форм в лист (сгладить)")
        self.hidden = QCheckBox("Невидимый текст (слой распознавания у сканов)")
        for w in (self.js, self.thumbs, self.meta, self.attach, self.annots, self.links, self.flatten, self.hidden):
            v3.addWidget(w)
        right.addWidget(g3)
        self.clean = QCheckBox("Удалить неиспользуемые объекты и уплотнить структуру файла")
        self.clean.setChecked(True)
        right.addWidget(self.clean)
        warn = QLabel("Результат сохраняется в новый файл — исходный не меняется.")
        warn.setStyleSheet("color: palette(mid);")
        right.addWidget(warn)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Оптимизировать и сохранить как…")
        bb.button(QDialogButtonBox.Cancel).setText("Отмена")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        right.addWidget(bb)
        lay.addLayout(right, 1)

        self.preset.currentIndexChanged.connect(self._apply_preset)
        for w in (self.dpi, self.thr):
            w.valueChanged.connect(self._custom)
        self.quality.valueChanged.connect(self._custom)
        self._loading = False
        self._apply_preset()
        self._request_audit()

    def _qtext(self, q: int) -> str:
        name = "максимальное" if q >= 92 else "высокое" if q >= 80 else "среднее" if q >= 60 else "низкое"
        return f"{q} ({name})"

    def _apply_preset(self) -> None:
        k = self.preset.currentData()
        self.qlbl.setText(self._qtext(self.quality.value()))
        if k == "custom":
            return
        p = O.PRESETS[k]
        self._loading = True
        self.dpi.setValue(p["dpi"])
        self.thr.setValue(p["threshold"])
        self.quality.setValue(p["quality"])
        self.qlbl.setText(self._qtext(p["quality"]))
        self._loading = False

    def _custom(self, *_):
        self.qlbl.setText(self._qtext(self.quality.value()))
        if not self._loading:
            self.preset.blockSignals(True)
            self.preset.setCurrentIndex(self.preset.findData("custom"))
            self.preset.blockSignals(False)

    def _request_audit(self) -> None:
        m = self.tab.model

        def done(res):
            sizes = res[2]
            total = len(m.main_source.data)
            rows = [(O.AUDIT_LABELS[k], v) for k, v in sorted(sizes.items(), key=lambda kv: -kv[1]) if v > 0]
            self.audit.setRowCount(len(rows))
            for i, (name, v) in enumerate(rows):
                self.audit.setItem(i, 0, QTableWidgetItem(name))
                it = QTableWidgetItem(fmt_size(v))
                it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.audit.setItem(i, 1, it)
                pc = QTableWidgetItem(f"{100 * v / max(1, total):.0f}")
                pc.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.audit.setItem(i, 2, pc)
            self.total.setText(f"Исходный файл: {fmt_size(total)}, листов: {m.main_source.page_count}")

        self.tab.service.submit("audit", (m.main,), sids=[m.main], priority=-50, callback=done,
                                errback=lambda msg: self.total.setText("Не удалось посчитать"))

    def options(self) -> dict:
        return {
            "images": self.images.isChecked(), "dpi": self.dpi.value(), "threshold": self.thr.value(),
            "quality": self.quality.value(), "to_gray": self.gray.isChecked(), "bitonal": self.bitonal.isChecked(),
            "subset_fonts": self.subset.isChecked(), "remove_javascript": self.js.isChecked(),
            "remove_thumbnails": self.thumbs.isChecked(), "remove_metadata": self.meta.isChecked(),
            "remove_attachments": self.attach.isChecked(), "remove_annots": self.annots.isChecked(),
            "flatten": self.flatten.isChecked(), "remove_links": self.links.isChecked(),
            "remove_hidden_text": self.hidden.isChecked(), "clean": self.clean.isChecked(),
        }
