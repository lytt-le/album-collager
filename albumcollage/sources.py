"""Album art providers: Apple iTunes Search API and MusicBrainz / Cover Art Archive.

Both are free and require no API key. Each provider returns a list of Candidate
objects with a small preview URL plus a lazy resolver for the full-resolution
image, so the UI can show a picker without downloading megabytes up front.
"""

from __future__ import annotations

import difflib
import io
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Iterable

import requests

from .config import USER_AGENT

ITUNES_SEARCH = "https://itunes.apple.com/search"
MB_SEARCH = "https://musicbrainz.org/ws/2/release-group"
CAA_BASE = "https://coverartarchive.org/release-group"

# iTunes artwork URLs end in "<w>x<h>bb.jpg". Swapping that segment asks the CDN
# for a different rendition; the first that returns 200 is the largest available.
ITUNES_RENDITIONS = ("100000x100000-999", "5000x5000bb", "3000x3000bb", "1200x1200bb")

TIMEOUT = 20

_session = requests.Session()
_session.headers.update({"User-Agent": USER_AGENT})

# MusicBrainz asks for no more than one request per second per client.
_mb_lock = threading.Lock()
_mb_last_call = 0.0


def _mb_throttle() -> None:
    global _mb_last_call
    with _mb_lock:
        wait = 1.05 - (time.monotonic() - _mb_last_call)
        if wait > 0:
            time.sleep(wait)
        _mb_last_call = time.monotonic()


@dataclass
class Candidate:
    """One possible cover for a search term."""

    source: str                  # "itunes" | "caa"
    artist: str
    album: str
    year: str = ""
    preview_url: str = ""        # small image for the picker grid
    full_url: str = ""           # direct full-res URL, when known up front
    ref: str = ""                # provider id (mbid / iTunes collectionId)
    extra: dict = field(default_factory=dict)

    @property
    def label(self) -> str:
        bits = f"{self.artist} - {self.album}"
        return f"{bits} ({self.year})" if self.year else bits

    @property
    def source_label(self) -> str:
        return {"itunes": "iTunes", "caa": "Cover Art Archive"}.get(self.source, self.source)


class SourceError(RuntimeError):
    pass


# --------------------------------------------------------------------------- #
# query helpers
# --------------------------------------------------------------------------- #

def split_query(term: str) -> tuple[str, str]:
    """Split 'Artist - Album' into its parts. Returns ('', term) if no separator."""
    for sep in (" - ", " – ", " — ", " -- ", " by "):
        if sep in term:
            left, right = term.split(sep, 1)
            if sep == " by ":          # "Album by Artist"
                return right.strip(), left.strip()
            return left.strip(), right.strip()
    return "", term.strip()


def _norm(text: str) -> str:
    text = re.sub(r"\((?:deluxe|remaster|remastered|expanded|anniversary)[^)]*\)", "", text, flags=re.I)
    text = re.sub(r"[^a-z0-9]+", " ", text.lower())
    return text.strip()


def score(candidate: Candidate, term: str) -> float:
    """0..1 similarity between a candidate and the user's search term."""
    artist, album = split_query(term)
    target = _norm(f"{artist} {album}") if artist else _norm(album)
    got = _norm(f"{candidate.artist} {candidate.album}")
    base = difflib.SequenceMatcher(None, target, got).ratio()
    if artist and _norm(candidate.artist) == _norm(artist):
        base = min(1.0, base + 0.15)
    if _norm(candidate.album) == _norm(album):
        base = min(1.0, base + 0.15)
    return base


# --------------------------------------------------------------------------- #
# iTunes
# --------------------------------------------------------------------------- #

def search_itunes(term: str, limit: int = 12) -> list[Candidate]:
    artist, album = split_query(term)
    query = f"{artist} {album}".strip() if artist else album
    try:
        resp = _session.get(
            ITUNES_SEARCH,
            params={"term": query, "entity": "album", "limit": limit, "media": "music"},
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        payload = resp.json()
    except (requests.RequestException, ValueError) as exc:
        raise SourceError(f"iTunes search failed: {exc}") from exc

    out: list[Candidate] = []
    for item in payload.get("results", []):
        art = item.get("artworkUrl100") or item.get("artworkUrl60") or ""
        if not art:
            continue
        out.append(
            Candidate(
                source="itunes",
                artist=item.get("artistName", "").strip(),
                album=item.get("collectionName", "").strip(),
                year=str(item.get("releaseDate", ""))[:4],
                preview_url=_itunes_rendition(art, "600x600bb"),
                full_url="",  # resolved lazily, largest rendition wins
                ref=str(item.get("collectionId", "")),
                extra={"art100": art},
            )
        )
    return out


def _itunes_rendition(url: str, rendition: str) -> str:
    """Replace the trailing size segment of an iTunes artwork URL.

    Artwork URLs look like `.../<hash>/<file>.jpg/100x100bb.jpg`; the final
    path segment names the rendition the CDN should produce.
    """
    return re.sub(r"/\d+x\d+[^/]*\.(?:jpg|jpeg|png)$", f"/{rendition}.jpg", url,
                  flags=re.IGNORECASE)


def _download_itunes_best(candidate: Candidate) -> bytes:
    art = candidate.extra.get("art100") or candidate.preview_url
    last_error: Exception | None = None
    for rendition in ITUNES_RENDITIONS:
        url = _itunes_rendition(art, rendition)
        try:
            resp = _session.get(url, timeout=TIMEOUT)
            if resp.status_code == 200 and resp.content:
                candidate.full_url = url
                return resp.content
        except requests.RequestException as exc:  # try the next rendition
            last_error = exc
    raise SourceError(f"Could not download iTunes artwork: {last_error or 'all renditions failed'}")


# --------------------------------------------------------------------------- #
# MusicBrainz / Cover Art Archive
# --------------------------------------------------------------------------- #

def search_caa(term: str, limit: int = 8) -> list[Candidate]:
    artist, album = split_query(term)
    if artist:
        query = f'artist:"{artist}" AND releasegroup:"{album}"'
    else:
        query = f'releasegroup:"{album}"'

    _mb_throttle()
    try:
        resp = _session.get(
            MB_SEARCH,
            params={"query": query, "fmt": "json", "limit": limit},
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        payload = resp.json()
    except (requests.RequestException, ValueError) as exc:
        raise SourceError(f"MusicBrainz search failed: {exc}") from exc

    out: list[Candidate] = []
    for group in payload.get("release-groups", []):
        mbid = group.get("id")
        if not mbid:
            continue
        credits = group.get("artist-credit") or []
        artist_name = "".join(
            (c.get("name") or c.get("artist", {}).get("name", "")) + (c.get("joinphrase") or "")
            for c in credits
        ).strip()
        out.append(
            Candidate(
                source="caa",
                artist=artist_name,
                album=(group.get("title") or "").strip(),
                year=str(group.get("first-release-date", ""))[:4],
                preview_url=f"{CAA_BASE}/{mbid}/front-500",
                full_url=f"{CAA_BASE}/{mbid}/front",
                ref=mbid,
                extra={"primary_type": group.get("primary-type") or ""},
            )
        )
    return out


def has_cover_art(candidate: Candidate) -> bool:
    """Cheap existence check - many MusicBrainz release groups have no artwork."""
    if candidate.source != "caa":
        return True
    try:
        resp = _session.head(candidate.preview_url, timeout=10, allow_redirects=True)
        if resp.status_code == 200:
            return True
        if resp.status_code == 404:
            return False
    except requests.RequestException:
        pass
    # Some mirrors reject HEAD; fall back to an aborted GET.
    try:
        with _session.get(candidate.preview_url, timeout=10, allow_redirects=True,
                          stream=True) as resp:
            return resp.status_code == 200
    except requests.RequestException:
        return False


def _download_caa(candidate: Candidate) -> bytes:
    for url in (candidate.full_url, candidate.preview_url):
        if not url:
            continue
        try:
            resp = _session.get(url, timeout=TIMEOUT, allow_redirects=True)
            if resp.status_code == 200 and resp.content:
                candidate.full_url = url
                return resp.content
        except requests.RequestException:
            continue
    raise SourceError("Cover Art Archive has no downloadable front cover for this release.")


# --------------------------------------------------------------------------- #
# public API
# --------------------------------------------------------------------------- #

def search(term: str, sources: Iterable[str] = ("itunes", "caa"),
           on_error: Callable[[str], None] | None = None,
           verify_caa: bool = True) -> list[Candidate]:
    """Search every enabled source and return candidates sorted best-match first."""
    term = term.strip()
    if not term:
        return []

    results: list[Candidate] = []
    order = {name: i for i, name in enumerate(sources)}

    for name in sources:
        try:
            if name == "itunes":
                results.extend(search_itunes(term))
            elif name == "caa":
                found = search_caa(term)
                if verify_caa:
                    found = [c for c in found if has_cover_art(c)]
                results.extend(found)
        except SourceError as exc:
            if on_error:
                on_error(str(exc))

    # Drop near-duplicates (same normalised artist+album from the same source).
    seen: set[tuple[str, str, str]] = set()
    unique: list[Candidate] = []
    for c in results:
        key = (c.source, _norm(c.artist), _norm(c.album))
        if key in seen:
            continue
        seen.add(key)
        unique.append(c)

    unique.sort(key=lambda c: (-score(c, term), order.get(c.source, 99)))
    return unique


def download(candidate: Candidate) -> bytes:
    """Fetch the largest available image bytes for a candidate."""
    if candidate.source == "itunes":
        return _download_itunes_best(candidate)
    return _download_caa(candidate)


def fetch_preview(url: str) -> bytes | None:
    """Fetch a small preview image; returns None instead of raising."""
    try:
        resp = _session.get(url, timeout=15, allow_redirects=True)
        if resp.status_code == 200 and resp.content:
            return resp.content
    except requests.RequestException:
        pass
    return None


def image_dimensions(data: bytes) -> tuple[int, int]:
    from PIL import Image
    with Image.open(io.BytesIO(data)) as img:
        return img.size
