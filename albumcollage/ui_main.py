"""Main application window."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from PyQt6.QtCore import QSize, Qt, QThreadPool
from PyQt6.QtGui import QAction, QIcon, QKeySequence, QPixmap
from PyQt6.QtWidgets import (
    QAbstractItemView, QCheckBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QListView, QMainWindow, QMenu, QMessageBox,
    QPushButton, QSizePolicy, QToolBar, QToolButton, QVBoxLayout, QWidget,
)

from . import __version__
from .config import load_settings, save_settings, storage_is_available, storage_root
from .library import Album, Library
from .sources import Candidate, available_sources, source_label
from .ui_dialogs import CandidatePicker, CollageDialog, placeholder_icon
from .ui_settings import SettingsDialog
from .workers import AutoAddTask, DownloadTask, SearchTask

TILE = 150


def open_in_file_manager(path: str | Path) -> None:
    path = str(path)
    if sys.platform == "win32":
        os.startfile(path)  # noqa: S606
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"AlbumCollage {__version__}")
        self.resize(1180, 800)

        self.settings = load_settings()
        self.library = Library()
        self._pending = 0
        self._live_tasks: set = set()

        self._build_ui()
        self._reload_grid()
        self._warn_if_storage_missing()

    def _warn_if_storage_missing(self) -> None:
        if storage_is_available():
            return
        configured = self.settings.get("storage_root", "")
        QMessageBox.warning(
            self, "Storage folder unavailable",
            f"The folder you chose for albums cannot be reached:\n\n{configured}\n\n"
            f"Using the default location for now. If this is an external or network "
            f"drive, reconnect it and restart, or pick a new folder in Settings.")

    # ---------------------------------------------------------------- ui --- #
    def _build_ui(self) -> None:
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Artist - Album   (e.g. Radiohead - In Rainbows)")
        self.search_box.setClearButtonEnabled(True)
        self.search_box.returnPressed.connect(self.add_album)

        add_button = QPushButton("Add album")
        add_button.clicked.connect(self.add_album)

        self.manual_check = QCheckBox("Pick cover manually")
        self.manual_check.setChecked(not self.settings.get("auto_pick", True))
        self.manual_check.setToolTip(
            "Off: grab the best match instantly.\nOn: show every candidate and choose.")
        self.manual_check.toggled.connect(self._save_mode)

        self.sources_label = QLabel()
        self.sources_label.setToolTip("Choose which services to search in Settings.")
        self.sources_label.setEnabled(False)

        top = QHBoxLayout()
        top.addWidget(self.search_box, 1)
        top.addWidget(add_button)
        top.addWidget(self.manual_check)

        under = QHBoxLayout()
        under.addWidget(self.sources_label, 1)

        self.grid = QListWidget()
        self.grid.setViewMode(QListView.ViewMode.IconMode)
        self.grid.setIconSize(QSize(TILE, TILE))
        self.grid.setGridSize(QSize(TILE + 24, TILE + 56))
        self.grid.setResizeMode(QListView.ResizeMode.Adjust)
        self.grid.setMovement(QListView.Movement.Static)
        self.grid.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.grid.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.grid.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.grid.setWordWrap(True)
        self.grid.setSpacing(6)
        self.grid.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.grid.customContextMenuRequested.connect(self._context_menu)
        self.grid.model().rowsMoved.connect(self._persist_order)

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.addLayout(top)
        layout.addLayout(under)
        layout.addWidget(self.grid, 1)
        self.setCentralWidget(central)

        self._build_toolbar()
        self.status = self.statusBar()
        self._refresh_sources_label()
        self._update_status()

    def _build_toolbar(self) -> None:
        bar = QToolBar("Main")
        bar.setMovable(False)
        self.addToolBar(bar)

        collage_action = QAction("Create collage", self)
        collage_action.setShortcut(QKeySequence("Ctrl+E"))
        collage_action.triggered.connect(self.open_collage_dialog)
        bar.addAction(collage_action)

        bar.addSeparator()

        import_action = QAction("Import image...", self)
        import_action.triggered.connect(self.import_images)
        bar.addAction(import_action)

        remove_action = QAction("Remove selected", self)
        remove_action.setShortcut(QKeySequence(QKeySequence.StandardKey.Delete))
        remove_action.triggered.connect(self.remove_selected)
        bar.addAction(remove_action)

        bar.addSeparator()

        sort_menu = QMenu("Sort", self)
        for label, key in (("By artist", "artist"), ("By album", "album"),
                           ("By year", "year"), ("By resolution", "resolution")):
            action = sort_menu.addAction(label)
            action.triggered.connect(lambda _checked=False, k=key: self._sort(k))
        sort_button = QToolButton(self)
        sort_button.setText("Sort")
        sort_button.setMenu(sort_menu)
        sort_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        bar.addWidget(sort_button)

        folder_action = QAction("Open data folder", self)
        folder_action.triggered.connect(lambda: open_in_file_manager(storage_root()))
        bar.addAction(folder_action)

        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        bar.addWidget(spacer)

        settings_action = QAction("Settings", self)
        settings_action.setShortcut(QKeySequence("Ctrl+,"))
        settings_action.triggered.connect(self.open_settings)
        bar.addAction(settings_action)

    # ------------------------------------------------------------- state --- #
    def _start(self, task) -> None:
        """Run a task on the pool, holding a reference until it signals finished."""
        self._live_tasks.add(task)
        task.signals.finished.connect(lambda t=task: self._live_tasks.discard(t))
        QThreadPool.globalInstance().start(task)

    def _enabled_sources(self) -> list[str]:
        """Sources the user ticked that also have the credentials they need."""
        return available_sources(self.settings) or ["itunes"]

    def _refresh_sources_label(self) -> None:
        active = self._enabled_sources()
        names = ", ".join(source_label(s) for s in active)
        skipped = [s for s in (self.settings.get("sources") or []) if s not in active]
        if skipped:
            names += f"   (skipping {', '.join(source_label(s) for s in skipped)} - no API key)"
        self.sources_label.setText(f"Searching: {names}")

    def _save_mode(self) -> None:
        self.settings["auto_pick"] = not self.manual_check.isChecked()
        save_settings(self.settings)

    def _reload_grid(self) -> None:
        self.grid.blockSignals(True)
        self.grid.clear()
        for album in self.library.albums:
            self.grid.addItem(self._make_item(album))
        self.grid.blockSignals(False)
        self._update_status()

    def _make_item(self, album: Album) -> QListWidgetItem:
        pix = QPixmap(str(album.thumb_path))
        icon = QIcon(pix) if not pix.isNull() else placeholder_icon(TILE)
        item = QListWidgetItem(icon, album.label)
        item.setTextAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        item.setToolTip(f"{album.label}\n{album.year}\n{album.resolution} - {album.source}")
        item.setData(Qt.ItemDataRole.UserRole, album.id)
        return item

    def _ordered_ids(self) -> list[str]:
        return [self.grid.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.grid.count())]

    def _persist_order(self, *_args) -> None:
        self.library.reorder(self._ordered_ids())
        self._update_status()

    def _update_status(self) -> None:
        count = len(self.library)
        busy = f"  -  {self._pending} in progress" if self._pending else ""
        self.status.showMessage(f"{count} album{'s' if count != 1 else ''} in library{busy}")

    def _sort(self, key: str) -> None:
        self.library.sort_by(key)
        self._reload_grid()

    # --------------------------------------------------------------- add --- #
    def add_album(self) -> None:
        term = self.search_box.text().strip()
        if not term:
            return
        self.search_box.clear()
        self._pending += 1
        self._update_status()

        if self.manual_check.isChecked():
            task = SearchTask(term, self._enabled_sources(), self.settings)
            task.signals.results.connect(self._show_picker)
            task.signals.warning.connect(lambda m: self.status.showMessage(m, 6000))
            task.signals.error.connect(self._task_failed)
        else:
            task = AutoAddTask(term, self._enabled_sources(), self.settings)
            task.signals.downloaded.connect(self._store_download)
            task.signals.error.connect(self._task_failed)
        self._start(task)

    def _show_picker(self, term: str, candidates: list) -> None:
        self._pending -= 1
        self._update_status()
        if not candidates:
            QMessageBox.information(self, "No results", f'Nothing found for "{term}".')
            return
        dialog = CandidatePicker(term, candidates, self)
        if not dialog.exec() or dialog.selected is None:
            return
        self._pending += 1
        self._update_status()
        task = DownloadTask(dialog.selected)
        task.signals.downloaded.connect(self._store_download)
        task.signals.error.connect(self._task_failed)
        self._start(task)

    def _store_download(self, candidate: Candidate, data: bytes) -> None:
        self._pending -= 1
        try:
            album = self.library.add_from_bytes(
                data,
                artist=candidate.artist,
                album=candidate.album,
                year=candidate.year,
                source=candidate.source_label,
                source_url=candidate.full_url,
            )
        except Exception as exc:  # noqa: BLE001
            self._update_status()
            QMessageBox.warning(self, "Could not save cover", str(exc))
            return
        self.grid.blockSignals(True)
        self.grid.addItem(self._make_item(album))
        self.grid.blockSignals(False)
        self._update_status()
        self.status.showMessage(f"Added {album.label} ({album.resolution})", 5000)

    def _task_failed(self, message: str) -> None:
        self._pending = max(0, self._pending - 1)
        self._update_status()
        self.status.showMessage(message, 8000)

    def import_images(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Import cover images", str(Path.home()),
            "Images (*.png *.jpg *.jpeg *.webp *.bmp *.tif *.tiff)")
        for path in paths:
            try:
                album = self.library.add_from_file(path)
            except Exception as exc:  # noqa: BLE001
                self.status.showMessage(f"{Path(path).name}: {exc}", 6000)
                continue
            self.grid.blockSignals(True)
            self.grid.addItem(self._make_item(album))
            self.grid.blockSignals(False)
        self._update_status()

    # ------------------------------------------------------------ remove --- #
    def remove_selected(self) -> None:
        items = self.grid.selectedItems()
        if not items:
            return
        if QMessageBox.question(
                self, "Remove albums",
                f"Remove {len(items)} album(s) from the library?") != QMessageBox.StandardButton.Yes:
            return
        for item in items:
            self.library.remove(item.data(Qt.ItemDataRole.UserRole))
        self._reload_grid()

    def _context_menu(self, pos) -> None:
        item = self.grid.itemAt(pos)
        menu = QMenu(self)
        if item is not None:
            album = self.library.get(item.data(Qt.ItemDataRole.UserRole))
            if album:
                menu.addAction("Open cover file",
                               lambda: open_in_file_manager(album.cover_path))
                menu.addAction(f"Resolution: {album.resolution}").setEnabled(False)
            menu.addAction("Remove", self.remove_selected)
            menu.addSeparator()
        menu.addAction("Create collage", self.open_collage_dialog)
        menu.exec(self.grid.mapToGlobal(pos))

    # ---------------------------------------------------------- settings --- #
    def open_settings(self) -> None:
        self._persist_order()
        dialog = SettingsDialog(self.settings, self)
        dialog.exec()
        # Storage moves take effect immediately, even if Save was not pressed.
        if dialog.storage_changed:
            self.settings = load_settings()
            self.library.rebind()
            self._reload_grid()
            self.status.showMessage(f"Albums now stored in {storage_root()}", 8000)
        else:
            self.settings = load_settings()
        self._sync_controls()

    def _sync_controls(self) -> None:
        """Push settings back onto the header controls after an external change."""
        self.manual_check.blockSignals(True)
        self.manual_check.setChecked(not self.settings.get("auto_pick", True))
        self.manual_check.blockSignals(False)
        self._refresh_sources_label()

    # ----------------------------------------------------------- collage --- #
    def open_collage_dialog(self) -> None:
        self._persist_order()
        if not self.library.albums:
            QMessageBox.information(self, "Empty library", "Add some albums first.")
            return
        dialog = CollageDialog(list(self.library.albums), self.settings, self)
        dialog.exec()
        save_settings(self.settings)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        self._persist_order()
        self._save_mode()
        super().closeEvent(event)
