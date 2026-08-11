"""Background tasks. Networking and image work must never run on the UI thread.

Every task emits `finished` last. Because queued connections preserve emission
order, the UI can safely drop its reference to a task once `finished` arrives -
which is what keeps the signal-carrier object alive long enough to deliver.
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QObject, QRunnable, pyqtSignal

from . import collage, sources
from .collage import CollageOptions
from .library import Album
from .sources import Candidate


class _Signals(QObject):
    results = pyqtSignal(str, list)      # term, list[Candidate]
    warning = pyqtSignal(str)
    error = pyqtSignal(str)
    preview = pyqtSignal(int, bytes)     # row index, image bytes
    downloaded = pyqtSignal(object, bytes)
    progress = pyqtSignal(int, int)
    finished_path = pyqtSignal(str)
    success = pyqtSignal(str)
    finished = pyqtSignal()


class BaseTask(QRunnable):
    def __init__(self):
        super().__init__()
        self.signals = _Signals()
        self.setAutoDelete(False)

    def run(self) -> None:
        try:
            self.work()
        except Exception as exc:  # noqa: BLE001 - never let a thread die silently
            self.signals.error.emit(str(exc))
        finally:
            self.signals.finished.emit()

    def work(self) -> None:  # pragma: no cover - overridden
        raise NotImplementedError


class SearchTask(BaseTask):
    def __init__(self, term: str, source_names: list[str], settings: dict):
        super().__init__()
        self.term = term
        self.source_names = source_names
        self.settings = dict(settings)

    def work(self) -> None:
        warnings: list[str] = []
        found = sources.search(self.term, self.source_names, settings=self.settings,
                               on_error=warnings.append)
        for message in warnings:
            self.signals.warning.emit(message)
        self.signals.results.emit(self.term, found)


class PreviewTask(BaseTask):
    def __init__(self, index: int, url: str):
        super().__init__()
        self.index = index
        self.url = url

    def work(self) -> None:
        data = sources.fetch_preview(self.url)
        if data:
            self.signals.preview.emit(self.index, data)


class DownloadTask(BaseTask):
    """Fetch the full-resolution image for one candidate."""

    def __init__(self, candidate: Candidate):
        super().__init__()
        self.candidate = candidate

    def work(self) -> None:
        try:
            data = sources.download(self.candidate)
        except Exception as exc:  # noqa: BLE001
            self.signals.error.emit(f"{self.candidate.label}: {exc}")
            return
        self.signals.downloaded.emit(self.candidate, data)


class AutoAddTask(BaseTask):
    """Search and download the best match in one go (auto mode)."""

    def __init__(self, term: str, source_names: list[str], settings: dict):
        super().__init__()
        self.term = term
        self.source_names = source_names
        self.settings = dict(settings)

    def work(self) -> None:
        warnings: list[str] = []
        found = sources.search(self.term, self.source_names, settings=self.settings,
                               on_error=warnings.append)
        if not found:
            detail = f" ({warnings[0]})" if warnings else ""
            self.signals.error.emit(f'No album art found for "{self.term}".{detail}')
            return
        best = found[0]
        try:
            data = sources.download(best)
        except Exception as exc:  # noqa: BLE001
            self.signals.error.emit(f"{best.label}: {exc}")
            return
        self.signals.downloaded.emit(best, data)


class SpotifyCheckTask(BaseTask):
    """Verify Spotify credentials without freezing the settings dialog."""

    def __init__(self, client_id: str, client_secret: str):
        super().__init__()
        self.client_id = client_id
        self.client_secret = client_secret

    def work(self) -> None:
        try:
            sources.check_spotify_credentials(self.client_id, self.client_secret)
        except Exception as exc:  # noqa: BLE001
            self.signals.error.emit(str(exc))
            return
        self.signals.success.emit("Spotify credentials work.")


class ExportTask(BaseTask):
    def __init__(self, albums: list[Album], opts: CollageOptions, out_path: str | Path):
        super().__init__()
        self.albums = albums
        self.opts = opts
        self.out_path = str(out_path)
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def work(self) -> None:
        try:
            path = collage.export_png(
                self.albums,
                self.opts,
                self.out_path,
                progress=lambda done, total: self.signals.progress.emit(done, total),
                should_cancel=lambda: self._cancelled,
            )
        except InterruptedError:
            return
        self.signals.finished_path.emit(str(path))
