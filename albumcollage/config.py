"""Application paths and persisted settings.

Two locations matter and they are deliberately separate:

* `app_dir()` - fixed, per-user, always writable. Holds settings.json only, so
  the app can always find its own configuration.
* `storage_root()` - where albums live. Defaults to `app_dir()` but the user can
  point it anywhere (an external drive, a synced folder) from the settings pane.
"""

from __future__ import annotations

import copy
import json
import os
import shutil
import sys
from pathlib import Path

APP_NAME = "AlbumCollage"
USER_AGENT = f"{APP_NAME}/1.0 (local desktop app)"


def app_dir() -> Path:
    """Writable per-user config directory (works the same frozen or not)."""
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = str(Path.home() / "Library" / "Application Support")
    else:
        base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    d = Path(base) / APP_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


SETTINGS_FILE = app_dir() / "settings.json"

DEFAULT_SETTINGS = {
    "theme": "dark",            # "dark" | "light" | "system"
    "storage_root": "",         # "" = use app_dir()
    "auto_pick": True,          # grab best match without showing the picker
    "sources": ["itunes", "caa", "deezer"],
    "spotify_client_id": "",
    "spotify_client_secret": "",
    "cell_size": 1000,
    "gap": 0,
    "margin": 0,
    "background": "#000000",
    "columns": 0,               # 0 = auto (square-ish)
    "last_export_dir": "",
}

_cache: dict | None = None


def load_settings(refresh: bool = False) -> dict:
    """Return a copy of the current settings, reading from disk on first use."""
    global _cache
    if _cache is None or refresh:
        merged = copy.deepcopy(DEFAULT_SETTINGS)
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as fh:
                stored = json.load(fh)
            if isinstance(stored, dict):
                merged.update(stored)
        except (OSError, ValueError):
            pass
        _cache = merged
    return copy.deepcopy(_cache)


def save_settings(settings: dict) -> None:
    global _cache
    merged = copy.deepcopy(DEFAULT_SETTINGS)
    merged.update(copy.deepcopy(settings))
    _cache = merged
    try:
        tmp = SETTINGS_FILE.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(merged, fh, indent=2)
        os.replace(tmp, SETTINGS_FILE)
    except OSError:
        pass


# --------------------------------------------------------------------------- #
# storage locations
# --------------------------------------------------------------------------- #

def storage_root() -> Path:
    """Folder holding library.json, covers/ and thumbs/."""
    configured = (load_settings().get("storage_root") or "").strip()
    root = Path(configured) if configured else app_dir()
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError:
        # Configured folder is gone (unplugged drive) - fall back rather than crash.
        root = app_dir()
    return root


def covers_dir() -> Path:
    d = storage_root() / "covers"
    d.mkdir(parents=True, exist_ok=True)
    return d


def thumbs_dir() -> Path:
    d = storage_root() / "thumbs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def library_file() -> Path:
    return storage_root() / "library.json"


def storage_is_available() -> bool:
    """False when a configured storage folder can no longer be reached."""
    configured = (load_settings().get("storage_root") or "").strip()
    if not configured:
        return True
    try:
        Path(configured).mkdir(parents=True, exist_ok=True)
        return True
    except OSError:
        return False


def storage_usage(root: Path | None = None) -> tuple[int, int]:
    """Return (file count, total bytes) for the covers and thumbs folders."""
    root = Path(root) if root else storage_root()
    count = 0
    total = 0
    for sub in ("covers", "thumbs"):
        folder = root / sub
        if not folder.is_dir():
            continue
        for item in folder.iterdir():
            if item.is_file():
                count += 1
                try:
                    total += item.stat().st_size
                except OSError:
                    pass
    return count, total


def human_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


class StorageMoveError(RuntimeError):
    pass


def set_storage_root(new_root: str | Path, move_existing: bool = True) -> Path:
    """Point storage at `new_root`, optionally relocating what is already there.

    Existing files are copied first and only removed once every copy succeeded,
    so an interrupted move never loses covers.
    """
    old = storage_root()
    target = Path(new_root).expanduser()

    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise StorageMoveError(f"Cannot create or write to {target}: {exc}") from exc

    probe = target / ".albumcollage-write-test"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        raise StorageMoveError(f"{target} is not writable: {exc}") from exc

    if target.resolve() == old.resolve():
        return old

    if move_existing:
        copied: list[Path] = []
        try:
            for sub in ("covers", "thumbs"):
                src = old / sub
                if not src.is_dir():
                    continue
                dst = target / sub
                dst.mkdir(parents=True, exist_ok=True)
                for item in src.iterdir():
                    if item.is_file():
                        shutil.copy2(item, dst / item.name)
                        copied.append(dst / item.name)
            src_lib = old / "library.json"
            if src_lib.is_file():
                shutil.copy2(src_lib, target / "library.json")
                copied.append(target / "library.json")
        except OSError as exc:
            for item in copied:              # roll back a partial copy
                try:
                    item.unlink(missing_ok=True)
                except OSError:
                    pass
            raise StorageMoveError(f"Could not copy your albums: {exc}") from exc

        # Copies all landed - now clear the originals.
        for sub in ("covers", "thumbs"):
            shutil.rmtree(old / sub, ignore_errors=True)
        try:
            (old / "library.json").unlink(missing_ok=True)
        except OSError:
            pass

    settings = load_settings()
    settings["storage_root"] = "" if target.resolve() == app_dir().resolve() else str(target)
    save_settings(settings)
    return storage_root()
