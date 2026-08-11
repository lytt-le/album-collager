"""Candidate picker and collage export dialogs."""

from __future__ import annotations

from pathlib import Path

from PIL import Image
from PyQt6.QtCore import QSize, Qt, QThreadPool
from PyQt6.QtGui import QColor, QIcon, QImage, QPixmap
from PyQt6.QtWidgets import (
    QColorDialog, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
    QFormLayout, QGroupBox, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
    QListView, QMessageBox, QProgressBar, QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from . import collage as collage_mod
from . import theme
from .collage import CollageOptions
from .library import Album
from .sources import Candidate
from .workers import ExportTask, PreviewTask


def pil_to_pixmap(img: Image.Image) -> QPixmap:
    rgb = img.convert("RGB")
    data = rgb.tobytes("raw", "RGB")
    qimg = QImage(data, rgb.width, rgb.height, rgb.width * 3, QImage.Format.Format_RGB888)
    return QPixmap.fromImage(qimg.copy())


def placeholder_icon(size: int = 180) -> QIcon:
    pix = QPixmap(size, size)
    pix.fill(QColor(theme.colours()["placeholder"]))
    return QIcon(pix)


def start_task(owner, task) -> None:
    """Run a task, keeping a reference on `owner` until it reports finished."""
    if not hasattr(owner, "_live_tasks"):
        owner._live_tasks = set()
    owner._live_tasks.add(task)
    task.signals.finished.connect(lambda t=task: owner._live_tasks.discard(t))
    QThreadPool.globalInstance().start(task)


class CandidatePicker(QDialog):
    """Grid of possible covers; previews stream in on background threads."""

    ICON = 190

    def __init__(self, term: str, candidates: list[Candidate], parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle(f'Choose cover - "{term}"')
        self.resize(880, 620)
        self.candidates = candidates
        self.selected: Candidate | None = None

        self.list = QListWidget(self)
        self.list.setViewMode(QListView.ViewMode.IconMode)
        self.list.setIconSize(QSize(self.ICON, self.ICON))
        self.list.setGridSize(QSize(self.ICON + 28, self.ICON + 78))
        self.list.setResizeMode(QListView.ResizeMode.Adjust)
        self.list.setMovement(QListView.Movement.Static)
        self.list.setWordWrap(True)
        self.list.setSpacing(6)
        self.list.itemDoubleClicked.connect(lambda _: self.accept())

        for index, cand in enumerate(candidates):
            item = QListWidgetItem(placeholder_icon(self.ICON),
                                   f"{cand.label}\n[{cand.source_label}]")
            item.setTextAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
            item.setData(Qt.ItemDataRole.UserRole, index)
            self.list.addItem(item)
        if candidates:
            self.list.setCurrentRow(0)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Double-click the cover you want, or select it and press OK."))
        layout.addWidget(self.list, 1)
        layout.addWidget(buttons)

        self._load_previews()

    def _load_previews(self) -> None:
        for index, cand in enumerate(self.candidates):
            if not cand.preview_url:
                continue
            task = PreviewTask(index, cand.preview_url)
            task.signals.preview.connect(self._set_preview)
            start_task(self, task)

    def _set_preview(self, index: int, data: bytes) -> None:
        if index >= self.list.count():
            return
        pix = QPixmap()
        if pix.loadFromData(data):
            item = self.list.item(index)
            if item is not None:
                item.setIcon(QIcon(pix))

    def accept(self) -> None:
        row = self.list.currentRow()
        if 0 <= row < len(self.candidates):
            self.selected = self.candidates[row]
        super().accept()


class CollageDialog(QDialog):
    """Configure the grid, preview it, and export a PNG."""

    def __init__(self, albums: list[Album], settings: dict, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Create collage")
        self.resize(1040, 720)
        self.albums = albums
        self.settings = settings
        self._task: ExportTask | None = None

        self.columns = QSpinBox()
        self.columns.setRange(0, 200)
        self.columns.setSpecialValueText("Auto (square)")
        self.columns.setValue(int(settings.get("columns", 0)))

        self.cell = QSpinBox()
        self.cell.setRange(100, 6000)
        self.cell.setSingleStep(100)
        self.cell.setSuffix(" px")
        self.cell.setValue(int(settings.get("cell_size", 1000)))

        self.gap = QSpinBox()
        self.gap.setRange(0, 500)
        self.gap.setSuffix(" px")
        self.gap.setValue(int(settings.get("gap", 0)))

        self.margin = QSpinBox()
        self.margin.setRange(0, 1000)
        self.margin.setSuffix(" px")
        self.margin.setValue(int(settings.get("margin", 0)))

        self.bg_button = QPushButton()
        self._bg = str(settings.get("background", "#000000"))
        self._paint_bg_button()
        self.bg_button.clicked.connect(self._choose_colour)

        self.downscale = QComboBox()
        self.downscale.addItem("Full resolution", 0)
        for label, px in (("Max 100 MP", 100_000_000), ("Max 50 MP", 50_000_000),
                          ("Max 25 MP", 25_000_000), ("Max 8 MP", 8_000_000)):
            self.downscale.addItem(label, px)

        form = QFormLayout()
        form.addRow("Columns", self.columns)
        form.addRow("Cover size", self.cell)
        form.addRow("Gap", self.gap)
        form.addRow("Outer margin", self.margin)
        form.addRow("Background", self.bg_button)
        form.addRow("Output limit", self.downscale)

        controls = QGroupBox("Grid")
        controls.setLayout(form)

        self.info = QLabel()
        self.info.setWordWrap(True)

        self.preview_label = QLabel("Preview")
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setMinimumSize(520, 520)
        palette = theme.colours()
        self.preview_label.setStyleSheet(
            f"background:{palette['base']}; border:1px solid {palette['border']};")

        refresh = QPushButton("Refresh preview")
        refresh.clicked.connect(self._refresh_preview)

        self.progress = QProgressBar()
        self.progress.setVisible(False)

        self.export_button = QPushButton("Export PNG...")
        self.export_button.setDefault(True)
        self.export_button.clicked.connect(self._export)
        close_button = QPushButton("Close")
        close_button.clicked.connect(self.reject)

        left = QVBoxLayout()
        left.addWidget(controls)
        left.addWidget(self.info)
        left.addWidget(refresh)
        left.addStretch(1)
        left.addWidget(self.progress)
        row = QHBoxLayout()
        row.addWidget(self.export_button)
        row.addWidget(close_button)
        left.addLayout(row)

        panel = QWidget()
        panel.setLayout(left)
        panel.setFixedWidth(330)

        main = QHBoxLayout(self)
        main.addWidget(panel)
        main.addWidget(self.preview_label, 1)

        for widget in (self.columns, self.cell, self.gap, self.margin):
            widget.valueChanged.connect(self._update_info)
        self._update_info()
        self._refresh_preview()

    # ------------------------------------------------------------ helpers -- #
    def options(self) -> CollageOptions:
        return CollageOptions(
            cell_size=self.cell.value(),
            gap=self.gap.value(),
            margin=self.margin.value(),
            columns=self.columns.value(),
            background=self._bg,
            max_pixels=int(self.downscale.currentData() or 0),
        )

    def _paint_bg_button(self) -> None:
        self.bg_button.setText(self._bg)
        self.bg_button.setStyleSheet(f"background:{self._bg}; padding:6px;")

    def _choose_colour(self) -> None:
        colour = QColorDialog.getColor(QColor(self._bg), self, "Background colour")
        if colour.isValid():
            self._bg = colour.name()
            self._paint_bg_button()
            self._update_info()

    def _update_info(self) -> None:
        opts = self.options()
        cols, rows = collage_mod.grid_shape(len(self.albums), opts.columns)
        width, height = collage_mod.output_size(len(self.albums), opts)
        megapixels = width * height / 1_000_000
        blanks = cols * rows - len(self.albums)
        note = f" ({blanks} empty slot{'s' if blanks != 1 else ''})" if blanks else ""
        self.info.setText(
            f"{len(self.albums)} albums - {cols} x {rows} grid{note}\n"
            f"Output: {width} x {height} px ({megapixels:.1f} MP)"
        )

    def _refresh_preview(self) -> None:
        if not self.albums:
            self.preview_label.setText("No albums in the library yet.")
            return
        try:
            img = collage_mod.preview(self.albums, self.options(),
                                      max_side=min(self.preview_label.width(),
                                                   self.preview_label.height()) or 520)
        except Exception as exc:  # noqa: BLE001
            self.preview_label.setText(f"Preview failed: {exc}")
            return
        self.preview_label.setPixmap(pil_to_pixmap(img))

    # ------------------------------------------------------------- export -- #
    def _export(self) -> None:
        if not self.albums:
            QMessageBox.information(self, "Nothing to export", "Add some albums first.")
            return
        start_dir = self.settings.get("last_export_dir") or str(Path.home())
        path, _ = QFileDialog.getSaveFileName(
            self, "Save collage", str(Path(start_dir) / "album-collage.png"), "PNG image (*.png)")
        if not path:
            return
        if not path.lower().endswith(".png"):
            path += ".png"

        self.settings["last_export_dir"] = str(Path(path).parent)
        self.settings.update({
            "columns": self.columns.value(),
            "cell_size": self.cell.value(),
            "gap": self.gap.value(),
            "margin": self.margin.value(),
            "background": self._bg,
        })

        self.export_button.setEnabled(False)
        self.progress.setVisible(True)
        self.progress.setRange(0, len(self.albums))
        self.progress.setValue(0)

        task = ExportTask(list(self.albums), self.options(), path)
        task.signals.progress.connect(lambda done, total: self.progress.setValue(done))
        task.signals.error.connect(self._export_failed)
        task.signals.finished_path.connect(self._export_done)
        self._task = task
        start_task(self, task)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if self._task is not None:
            self._task.cancel()
        super().closeEvent(event)

    def _export_failed(self, message: str) -> None:
        self.progress.setVisible(False)
        self.export_button.setEnabled(True)
        QMessageBox.critical(self, "Export failed", message)

    def _export_done(self, path: str) -> None:
        self.progress.setVisible(False)
        self.export_button.setEnabled(True)
        box = QMessageBox(self)
        box.setWindowTitle("Collage saved")
        box.setText(f"Saved to:\n{path}")
        open_folder = box.addButton("Open folder", QMessageBox.ButtonRole.ActionRole)
        box.addButton(QMessageBox.StandardButton.Ok)
        box.exec()
        if box.clickedButton() is open_folder:
            import os
            import subprocess
            import sys
            folder = str(Path(path).parent)
            if sys.platform == "win32":
                os.startfile(folder)  # noqa: S606
            elif sys.platform == "darwin":
                subprocess.Popen(["open", folder])
            else:
                subprocess.Popen(["xdg-open", folder])
