"""Persistent album library: metadata in JSON, full-res covers cached on disk."""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from PIL import Image

from .config import covers_dir, library_file, thumbs_dir

THUMB_SIZE = 320


@dataclass
class Album:
    id: str
    artist: str
    album: str
    year: str = ""
    source: str = ""
    source_url: str = ""
    cover_file: str = ""       # filename inside covers_dir()
    thumb_file: str = ""       # filename inside thumbs_dir()
    width: int = 0
    height: int = 0
    tags: list[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        return f"{self.artist} - {self.album}" if self.artist else self.album

    @property
    def cover_path(self) -> Path:
        return covers_dir() / self.cover_file

    @property
    def thumb_path(self) -> Path:
        return thumbs_dir() / self.thumb_file

    @property
    def resolution(self) -> str:
        return f"{self.width}x{self.height}" if self.width else "unknown"


def _safe_name(text: str, limit: int = 60) -> str:
    text = re.sub(r"[^\w\s.-]", "", text, flags=re.UNICODE).strip()
    text = re.sub(r"\s+", "_", text)
    return text[:limit] or "album"


class Library:
    """In-memory ordered list of albums, backed by a JSON file."""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else library_file()
        self.albums: list[Album] = []
        self.load()

    def rebind(self) -> None:
        """Re-read from whatever the current storage location is."""
        self.path = library_file()
        self.load()

    # ---------------------------------------------------------------- io --- #
    def load(self) -> None:
        self.albums = []
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
        except (OSError, ValueError):
            return
        for item in raw.get("albums", []):
            try:
                known = {f: item.get(f) for f in Album.__dataclass_fields__ if f in item}
                known.setdefault("id", uuid.uuid4().hex)
                known.setdefault("tags", [])
                self.albums.append(Album(**known))
            except TypeError:
                continue
        # Drop entries whose cover file vanished.
        self.albums = [a for a in self.albums if a.cover_file and a.cover_path.exists()]

    def save(self) -> None:
        payload = {"version": 1, "albums": [asdict(a) for a in self.albums]}
        tmp = self.path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, ensure_ascii=False)
        os.replace(tmp, self.path)

    # ------------------------------------------------------------ mutate --- #
    def contains(self, artist: str, album: str) -> bool:
        key = (artist.strip().lower(), album.strip().lower())
        return any((a.artist.strip().lower(), a.album.strip().lower()) == key for a in self.albums)

    def add_from_bytes(self, data: bytes, artist: str, album: str, year: str = "",
                       source: str = "", source_url: str = "") -> Album:
        """Store image bytes as the full-res cover plus a thumbnail, and register it."""
        with Image.open(io.BytesIO(data)) as img:
            img.load()
            width, height = img.size
            fmt = (img.format or "JPEG").upper()
            ext = {"PNG": ".png", "WEBP": ".webp", "GIF": ".gif",
                   "TIFF": ".tif", "BMP": ".bmp"}.get(fmt, ".jpg")
            digest = hashlib.sha1(data).hexdigest()[:10]
            stem = f"{_safe_name(artist)}-{_safe_name(album)}-{digest}"

            cover_name = stem + ext
            with open(covers_dir() / cover_name, "wb") as fh:
                fh.write(data)

            thumb = img.convert("RGB")
            thumb.thumbnail((THUMB_SIZE, THUMB_SIZE), Image.LANCZOS)
            thumb_name = stem + "_thumb.jpg"
            thumb.save(thumbs_dir() / thumb_name, "JPEG", quality=88)

        record = Album(
            id=uuid.uuid4().hex,
            artist=artist.strip(),
            album=album.strip(),
            year=year,
            source=source,
            source_url=source_url,
            cover_file=cover_name,
            thumb_file=thumb_name,
            width=width,
            height=height,
        )
        self.albums.append(record)
        self.save()
        return record

    def add_from_file(self, path: str | Path) -> Album:
        path = Path(path)
        data = path.read_bytes()
        return self.add_from_bytes(data, artist="", album=path.stem, source="local",
                                   source_url=str(path))

    def remove(self, album_id: str, delete_files: bool = True) -> None:
        keep: list[Album] = []
        for a in self.albums:
            if a.id != album_id:
                keep.append(a)
                continue
            if delete_files:
                for p in (a.cover_path, a.thumb_path):
                    try:
                        p.unlink(missing_ok=True)
                    except OSError:
                        pass
        self.albums = keep
        self.save()

    def reorder(self, ordered_ids: list[str]) -> None:
        index = {aid: i for i, aid in enumerate(ordered_ids)}
        self.albums.sort(key=lambda a: index.get(a.id, len(index)))
        self.save()

    def sort_by(self, key: str) -> None:
        if key == "artist":
            self.albums.sort(key=lambda a: (a.artist.lower(), a.year, a.album.lower()))
        elif key == "album":
            self.albums.sort(key=lambda a: a.album.lower())
        elif key == "year":
            self.albums.sort(key=lambda a: (a.year or "9999", a.artist.lower()))
        elif key == "resolution":
            self.albums.sort(key=lambda a: -(a.width * a.height))
        self.save()

    def get(self, album_id: str) -> Album | None:
        return next((a for a in self.albums if a.id == album_id), None)

    def __len__(self) -> int:
        return len(self.albums)
