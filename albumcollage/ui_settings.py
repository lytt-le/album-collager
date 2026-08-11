"""Settings pane: appearance, art sources, and where albums are stored on disk."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
    QFormLayout, QFrame, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from . import config, sources, theme
from .ui_dialogs import start_task
from .workers import SpotifyCheckTask


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

        body = QWidget()
        inner = QVBoxLayout(body)
        inner.setContentsMargins(0, 0, 12, 0)
        inner.addWidget(self._appearance_group())
        inner.addWidget(self._sources_group())
        inner.addWidget(self._storage_group())
        inner.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidget(body)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel, self)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(scroll, 1)
        layout.addWidget(buttons)
        self.resize(640, 760)

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

    # ----------------------------------------------------------- sources --- #
    def _sources_group(self) -> QGroupBox:
        """One row per provider: tick box, what it gives you, and any key it needs."""
        self.source_checks: dict[str, QCheckBox] = {}
        self.credential_edits: dict[str, QLineEdit] = {}

        enabled = set(self.settings.get("sources") or [])
        outer = QVBoxLayout()

        intro = QLabel("Every ticked source is searched, and results are ranked by how "
                       "well they match what you typed.")
        intro.setWordWrap(True)
        outer.addWidget(intro)

        for info in sources.SOURCES:
            outer.addWidget(self._source_row(info, info.id in enabled))

        self.auto_check = QCheckBox("Add the best match automatically")
        self.auto_check.setChecked(bool(self.settings.get("auto_pick", True)))
        self.auto_check.setToolTip(
            "Ticked: pressing Enter grabs the top-ranked cover straight away.\n"
            "Unticked: every candidate is shown so you can choose.")
        outer.addSpacing(6)
        outer.addWidget(self.auto_check)

        group = QGroupBox("Sources")
        group.setLayout(outer)
        return group

    def _source_row(self, info: sources.SourceInfo, checked: bool) -> QWidget:
        frame = QFrame()
        frame.setFrameShape(QFrame.Shape.StyledPanel)
        grid = QGridLayout(frame)
        grid.setContentsMargins(10, 8, 10, 8)

        box = QCheckBox(f"{info.label}  -  {info.resolution}")
        box.setChecked(checked)
        self.source_checks[info.id] = box
        grid.addWidget(box, 0, 0, 1, 2)

        if info.note:
            note = QLabel(info.note)
            note.setWordWrap(True)
            note.setEnabled(False)
            grid.addWidget(note, 1, 0, 1, 2)

        row = 2
        for key, label, secret in info.credentials:
            edit = QLineEdit(str(self.settings.get(key, "") or ""))
            edit.setPlaceholderText(f"Paste your {label.lower()} here")
            if secret:
                edit.setEchoMode(QLineEdit.EchoMode.Password)
            edit.textChanged.connect(lambda _t, i=info: self._refresh_source_state(i))
            self.credential_edits[key] = edit
            grid.addWidget(QLabel(label), row, 0)
            grid.addWidget(edit, row, 1)
            grid.setColumnStretch(1, 1)
            row += 1

        if info.credentials:
            hint = QLabel(info.help_text)
            hint.setWordWrap(True)
            hint.setEnabled(False)
            grid.addWidget(hint, row, 0, 1, 2)
            row += 1

            actions = QHBoxLayout()
            if info.help_url:
                get_key = QPushButton("Get a key...")
                get_key.clicked.connect(
                    lambda _c=False, url=info.help_url: QDesktopServices.openUrl(QUrl(url)))
                actions.addWidget(get_key)
            if info.id == "spotify":
                test = QPushButton("Test connection")
                test.clicked.connect(self._test_spotify)
                actions.addWidget(test)
            actions.addStretch(1)
            grid.addLayout(actions, row, 0, 1, 2)

        self._refresh_source_state(info)
        return frame

    def _current_credentials(self) -> dict:
        """Settings as they stand in the dialog right now, keys included."""
        merged = dict(self.settings)
        for key, edit in self.credential_edits.items():
            merged[key] = edit.text().strip()
        return merged

    def _refresh_source_state(self, info: sources.SourceInfo) -> None:
        """Grey out a source until it has the credentials it needs."""
        box = self.source_checks.get(info.id)
        if box is None:
            return
        missing = info.missing_credentials(self._current_credentials())
        if missing:
            box.setEnabled(False)
            box.setChecked(False)
            box.setToolTip("Add " + " and ".join(missing) + " below to enable this source.")
        else:
            box.setEnabled(True)
            box.setToolTip("")

    def _test_spotify(self) -> None:
        creds = self._current_credentials()
        client_id = creds.get("spotify_client_id", "")
        client_secret = creds.get("spotify_client_secret", "")
        if not client_id or not client_secret:
            QMessageBox.information(self, "Spotify",
                                    "Enter both the Client ID and the Client secret first.")
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        task = SpotifyCheckTask(client_id, client_secret)
        task.signals.success.connect(
            lambda msg: QMessageBox.information(self, "Spotify", msg))
        task.signals.error.connect(
            lambda msg: QMessageBox.warning(self, "Spotify", msg))
        task.signals.finished.connect(QApplication.restoreOverrideCursor)
        start_task(self, task)

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
        chosen = [info.id for info in sources.SOURCES
                  if self.source_checks[info.id].isChecked()]
        if not chosen:
            QMessageBox.warning(self, "No sources", "Tick at least one source to search.")
            return

        self.settings["theme"] = self.theme_box.currentData()
        self.settings["sources"] = chosen
        self.settings["auto_pick"] = self.auto_check.isChecked()
        for key, edit in self.credential_edits.items():
            self.settings[key] = edit.text().strip()
        config.save_settings(self.settings)
        self.accept()

    def reject(self) -> None:
        # Storage moves already happened on disk; only the live theme preview
        # needs undoing.
        app = QApplication.instance()
        if app is not None:
            theme.apply_theme(app, self._original_theme)
        super().reject()
