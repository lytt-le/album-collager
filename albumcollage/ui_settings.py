"""Settings pane: appearance and where albums are stored on disk."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
    QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPushButton, QVBoxLayout, QWidget,
)

from . import config, theme


class SettingsDialog(QDialog):
    """Changes apply when you press Save.

    Theme is previewed live as you pick it, and reverted if you cancel, because
    a colour scheme is much easier to judge by looking than by reading a label.
    """

    def __init__(self, settings: dict, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(560)
        self.settings = settings
        self._original_theme = settings.get("theme", "dark")
        self.storage_changed = False

        layout = QVBoxLayout(self)
        layout.addWidget(self._appearance_group())
        layout.addWidget(self._storage_group())
        layout.addStretch(1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel, self)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    # -------------------------------------------------------- appearance --- #
    def _appearance_group(self) -> QGroupBox:
        self.theme_box = QComboBox()
        for label, value in (("Dark", "dark"), ("Light", "light"),
                             ("Match system", "system")):
            self.theme_box.addItem(label, value)
        index = self.theme_box.findData(self._original_theme)
        self.theme_box.setCurrentIndex(index if index >= 0 else 0)
        self.theme_box.currentIndexChanged.connect(self._preview_theme)

        note = QLabel("Some panels pick up their colours when they next open.")
        note.setWordWrap(True)
        note.setEnabled(False)

        form = QFormLayout()
        form.addRow("Theme", self.theme_box)
        form.addRow("", note)

        group = QGroupBox("Appearance")
        group.setLayout(form)
        return group

    def _preview_theme(self) -> None:
        app = QApplication.instance()
        if app is not None:
            theme.apply_theme(app, self.theme_box.currentData())

    # ----------------------------------------------------------- storage --- #
    def _storage_group(self) -> QGroupBox:
        self.path_edit = QLineEdit(str(config.storage_root()))
        self.path_edit.setReadOnly(True)

        browse = QPushButton("Change...")
        browse.clicked.connect(self._browse)
        default_button = QPushButton("Use default")
        default_button.clicked.connect(self._use_default)
        open_button = QPushButton("Open folder")
        open_button.clicked.connect(self._open_folder)

        row = QHBoxLayout()
        row.addWidget(self.path_edit, 1)
        row.addWidget(browse)
        row.addWidget(default_button)
        row.addWidget(open_button)

        self.move_check = QCheckBox("Move existing albums to the new folder")
        self.move_check.setChecked(True)
        self.move_check.setToolTip(
            "Copies covers and thumbnails across first, then removes the originals.\n"
            "Unticked, the app starts a fresh library in the new folder and leaves\n"
            "your current one untouched.")

        self.usage_label = QLabel()
        self.usage_label.setWordWrap(True)
        self._refresh_usage()

        inner = QVBoxLayout()
        inner.addWidget(QLabel("Albums, thumbnails and the library index are kept here:"))
        inner.addLayout(row)
        inner.addWidget(self.move_check)
        inner.addWidget(self.usage_label)

        group = QGroupBox("Storage")
        group.setLayout(inner)
        return group

    def _refresh_usage(self) -> None:
        count, total = config.storage_usage()
        self.usage_label.setText(
            f"Currently {count} file{'s' if count != 1 else ''} using "
            f"{config.human_size(total)}.")

    def _browse(self) -> None:
        chosen = QFileDialog.getExistingDirectory(
            self, "Choose where to save albums", self.path_edit.text(),
            QFileDialog.Option.ShowDirsOnly)
        if chosen:
            self._apply_storage(chosen)

    def _use_default(self) -> None:
        self._apply_storage(config.app_dir())

    def _open_folder(self) -> None:
        from .ui_main import open_in_file_manager
        open_in_file_manager(self.path_edit.text())

    def _apply_storage(self, new_root: str | Path) -> None:
        target = Path(new_root)
        current = config.storage_root()
        if target.resolve() == current.resolve():
            return

        count, total = config.storage_usage()
        if count:
            if self.move_check.isChecked():
                question = (f"Move {count} files ({config.human_size(total)}) from\n\n"
                            f"{current}\n\nto\n\n{target}?")
            else:
                question = (f"Start a new, empty library in\n\n{target}\n\n"
                            f"Your existing {count} files stay where they are, in\n\n"
                            f"{current}\n\nContinue?")
            answer = QMessageBox.question(
                self, "Change storage location", question,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel)
            if answer != QMessageBox.StandardButton.Yes:
                return

        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            root = config.set_storage_root(target, move_existing=self.move_check.isChecked())
        except config.StorageMoveError as exc:
            QMessageBox.critical(self, "Could not change location", str(exc))
            return
        finally:
            QApplication.restoreOverrideCursor()

        self.path_edit.setText(str(root))
        self.settings["storage_root"] = config.load_settings().get("storage_root", "")
        self.storage_changed = True
        self._refresh_usage()

    # ------------------------------------------------------------ finish --- #
    def _save(self) -> None:
        self.settings["theme"] = self.theme_box.currentData()
        config.save_settings(self.settings)
        self.accept()

    def reject(self) -> None:
        # Storage moves already happened on disk; only the live theme preview
        # needs undoing.
        app = QApplication.instance()
        if app is not None:
            theme.apply_theme(app, self._original_theme)
        super().reject()
