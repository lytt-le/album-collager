"""Album art providers.

Four providers are supported. Each returns `Candidate` objects carrying a small
preview URL plus enough information to fetch the full-resolution image later, so
the picker can show a grid without downloading megabytes up front.

| id       | key needed | typical maximum                                  |
|----------|------------|--------------------------------------------------|
| itunes   | no         | 1400-3000 px, occasionally larger                |
| caa      | no         | 500-4000 px community scans                      |
| deezer   | no         | 1000 px                                          |
| spotify  | yes        | 640 px (a hard cap on Spotify's side)            |
"""

from __future__ import annotations

import base64
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
DEEZER_SEARCH = "https://api.deezer.com/search/album"
SPOTIFY_TOKEN = "https://accounts.spotify.com/api/token"
SPOTIFY_SEARCH = "https://api.spotify.com/v1/search"

# iTunes artwork URLs end in "<w>x<h>bb.jpg". Swapping that segment asks the CDN
# for a different rendition; the first that returns 200 is the largest available.
ITUNES_RENDITIONS = ("100000x100000-999", "5000x5000bb", "3000x3000bb", "1200x1200bb")

# Deezer's CDN resizes on demand and will happily upscale, so asking for more
# than the documented 1000 px maximum yields fake detail, not real detail.
DEEZER_MAX = 1000

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


# --------------------------------------------------------------------------- #
# registry
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class SourceInfo:
    """Everything the UI needs to describe and configure a provider."""

    id: str
    label: str
    resolution: str
    note: str = ""
    credentials: tuple[tuple[str, str, bool], ...] = ()   # (settings key, label, secret)
    help_url: str = ""
    help_text: str = ""

    def missing_credentials(self, settings: dict) -> list[str]:
        return [label for key, label, _ in self.credentials
                if not str(settings.get(key, "") or "").strip()]

    def is_ready(self, settings: dict) -> bool:
        return not self.missing_credentials(settings)


SOURCES: tuple[SourceInfo, ...] = (
    SourceInfo(
        id="itunes",
        label="iTunes",
        resolution="1400-3000 px",
        note="Fast, broad coverage of mainstream releases.",
    ),
    SourceInfo(
        id="caa",
        label="Cover Art Archive",
        resolution="500-4000 px",
        note="Community scans via MusicBrainz. Best for obscure and vinyl-only "
             "releases, but rate-limited to one request per second.",
    ),
    SourceInfo(
        id="deezer",
        label="Deezer",
        resolution="up to 1000 px",
        note="No key needed. Good catalogue coverage, including plenty that "
             "iTunes misses.",
    ),
    SourceInfo(
        id="spotify",
        label="Spotify",
        resolution="640 px maximum",
        note="Excellent matching, but Spotify caps artwork at 640 px, which is "
             "small for a large collage. Best used as a fallback.",
        credentials=(
            ("spotify_client_id", "Client ID", False),
            ("spotify_client_secret", "Client secret", True),
        ),
        help_url="https://developer.spotify.com/dashboard",
        help_text="Sign in at the Spotify developer dashboard, create an app "
                  "(any name, any redirect URI), then copy its Client ID and "
                  "Client secret here. It is free and takes about a minute.",
    ),
)

SOURCE_BY_ID = {s.id: s for s in SOURCES}


def source_label(source_id: str) -> str:
    info = SOURCE_BY_ID.get(source_id)
    return info.label if info else source_id


def available_sources(settings: dict) -> list[str]:
    """Enabled sources that also have whatever credentials they need."""
    enabled = settings.get("sources") or []
    return [sid for sid in enabled
            if sid in SOURCE_BY_ID and SOURCE_BY_ID[sid].is_ready(settings)]


# --------------------------------------------------------------------------- #
# candidates
# --------------------------------------------------------------------------- #

@dataclass
class Candidate:
    """One possible cover for a search term."""

    source: str
    artist: str
    album: str
    year: str = ""
    preview_url: str = ""        # small image for the picker grid
    full_url: str = ""           # direct full-res URL, when known up front
    ref: str = ""                # provider id
    extra: dict = field(default_factory=dict)

    @property
    def label(self) -> str:
        bits = f"{self.artist} - {self.album}"
        return f"{bits} ({self.year})" if self.year else bits

    @property
    def source_label(self) -> str:
        return source_label(self.source)


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


def _get_json(url: str, **kwargs) -> dict:
    resp = _session.get(url, timeout=TIMEOUT, **kwargs)
    resp.raise_for_status()
    return resp.json()


# --------------------------------------------------------------------------- #
# iTunes
# --------------------------------------------------------------------------- #

def search_itunes(term: str, settings: dict, limit: int = 12) -> list[Candidate]:
    artist, album = split_query(term)
    query = f"{artist} {album}".strip() if artist else album
    try:
        payload = _get_json(
            ITUNES_SEARCH,
            params={"term": query, "entity": "album", "limit": limit, "media": "music"},
        )
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

def search_caa(term: str, settings: dict, limit: int = 8) -> list[Candidate]:
    artist, album = split_query(term)
    if artist:
        query = f'artist:"{artist}" AND releasegroup:"{album}"'
    else:
        query = f'releasegroup:"{album}"'

    _mb_throttle()
    try:
        payload = _get_json(MB_SEARCH, params={"query": query, "fmt": "json", "limit": limit})
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
# Deezer
# --------------------------------------------------------------------------- #

def _deezer_rendition(url: str, size: int) -> str:
    """Rewrite the size segment of a Deezer cover URL (`.../1000x1000-000000-80-0-0.jpg`)."""
    return re.sub(r"/\d+x\d+(?:-[^/]*)?\.jpg$", f"/{size}x{size}-000000-100-0-0.jpg", url,
                  flags=re.IGNORECASE)


def search_deezer(term: str, settings: dict, limit: int = 12) -> list[Candidate]:
    artist, album = split_query(term)
    query = f'artist:"{artist}" album:"{album}"' if artist else album
    try:
        payload = _get_json(DEEZER_SEARCH, params={"q": query, "limit": limit})
    except (requests.RequestException, ValueError) as exc:
        raise SourceError(f"Deezer search failed: {exc}") from exc

    if isinstance(payload.get("error"), dict) and payload["error"]:
        raise SourceError(f"Deezer error: {payload['error'].get('message', 'unknown')}")

    out: list[Candidate] = []
    for item in payload.get("data", []):
        cover = item.get("cover_xl") or item.get("cover_big") or item.get("cover_medium") or ""
        if not cover:
            continue
        out.append(
            Candidate(
                source="deezer",
                artist=(item.get("artist") or {}).get("name", "").strip(),
                album=(item.get("title") or "").strip(),
                year="",  # not present in Deezer's album search results
                preview_url=_deezer_rendition(cover, 500),
                full_url=_deezer_rendition(cover, DEEZER_MAX),
                ref=str(item.get("id", "")),
            )
        )
    return out


# --------------------------------------------------------------------------- #
# Spotify
# --------------------------------------------------------------------------- #

_spotify_lock = threading.Lock()
_spotify_token: dict = {"value": "", "expires": 0.0, "key": ()}


def _spotify_access_token(client_id: str, client_secret: str) -> str:
    """Client-credentials token, cached until shortly before it expires."""
    key = (client_id, client_secret)
    with _spotify_lock:
        if _spotify_token["key"] == key and time.time() < _spotify_token["expires"]:
            return _spotify_token["value"]

        basic = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
        try:
            resp = _session.post(
                SPOTIFY_TOKEN,
                data={"grant_type": "client_credentials"},
                headers={"Authorization": f"Basic {basic}",
                         "Content-Type": "application/x-www-form-urlencoded"},
                timeout=TIMEOUT,
            )
        except requests.RequestException as exc:
            raise SourceError(f"Could not reach Spotify: {exc}") from exc

        if resp.status_code == 400:
            raise SourceError("Spotify rejected your Client ID or secret. "
                              "Check both values in Settings.")
        try:
            resp.raise_for_status()
            payload = resp.json()
        except (requests.RequestException, ValueError) as exc:
            raise SourceError(f"Spotify token request failed: {exc}") from exc

        token = payload.get("access_token", "")
        if not token:
            raise SourceError("Spotify returned no access token.")
        _spotify_token.update({
            "value": token,
            "key": key,
            "expires": time.time() + float(payload.get("expires_in", 3600)) - 60,
        })
        return token


def search_spotify(term: str, settings: dict, limit: int = 12) -> list[Candidate]:
    client_id = str(settings.get("spotify_client_id", "") or "").strip()
    client_secret = str(settings.get("spotify_client_secret", "") or "").strip()
    if not client_id or not client_secret:
        raise SourceError("Spotify needs a Client ID and secret - add them in Settings.")

    artist, album = split_query(term)
    query = f'album:"{album}" artist:"{artist}"' if artist else album
    token = _spotify_access_token(client_id, client_secret)

    try:
        resp = _session.get(
            SPOTIFY_SEARCH,
            params={"q": query, "type": "album", "limit": limit},
            headers={"Authorization": f"Bearer {token}"},
            timeout=TIMEOUT,
        )
        if resp.status_code == 401:                 # token went stale early
            _spotify_token.update({"expires": 0.0})
            token = _spotify_access_token(client_id, client_secret)
            resp = _session.get(
                SPOTIFY_SEARCH,
                params={"q": query, "type": "album", "limit": limit},
                headers={"Authorization": f"Bearer {token}"},
                timeout=TIMEOUT,
            )
        if resp.status_code == 429:
            raise SourceError("Spotify is rate-limiting this app; try again shortly.")
        resp.raise_for_status()
        payload = resp.json()
    except SourceError:
        raise
    except (requests.RequestException, ValueError) as exc:
        raise SourceError(f"Spotify search failed: {exc}") from exc

    out: list[Candidate] = []
    for item in (payload.get("albums") or {}).get("items", []):
        images = item.get("images") or []
        if not images:
            continue
        images = sorted(images, key=lambda i: -(i.get("width") or 0))
        preview = next((i for i in images if (i.get("width") or 0) <= 400), images[-1])
        out.append(
            Candidate(
                source="spotify",
                artist=", ".join(a.get("name", "") for a in item.get("artists", [])).strip(),
                album=(item.get("name") or "").strip(),
                year=str(item.get("release_date", ""))[:4],
                preview_url=preview.get("url", ""),
                full_url=images[0].get("url", ""),
                ref=str(item.get("id", "")),
            )
        )
    return out


# --------------------------------------------------------------------------- #
# dispatch
# --------------------------------------------------------------------------- #

_SEARCHERS: dict[str, Callable[[str, dict], list[Candidate]]] = {
    "itunes": search_itunes,
    "caa": search_caa,
    "deezer": search_deezer,
    "spotify": search_spotify,
}


def _download_direct(candidate: Candidate) -> bytes:
    """Generic fetch for providers that hand out a usable URL up front."""
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
    raise SourceError(f"Could not download artwork from {candidate.source_label}.")


_DOWNLOADERS: dict[str, Callable[[Candidate], bytes]] = {
    "itunes": _download_itunes_best,
    "caa": _download_caa,
}


def search(term: str, sources: Iterable[str] = ("itunes", "caa"),
           settings: dict | None = None,
           on_error: Callable[[str], None] | None = None,
           verify_caa: bool = True) -> list[Candidate]:
    """Search every enabled source and return candidates sorted best-match first."""
    term = term.strip()
    if not term:
        return []

    settings = settings or {}
    source_ids = [s for s in sources if s in _SEARCHERS]
    results: list[Candidate] = []
    order = {name: i for i, name in enumerate(source_ids)}

    for name in source_ids:
        try:
            found = _SEARCHERS[name](term, settings)
            if name == "caa" and verify_caa:
                found = [c for c in found if has_cover_art(c)]
            results.extend(found)
        except SourceError as exc:
            if on_error:
                on_error(str(exc))
        except Exception as exc:  # noqa: BLE001 - one bad provider must not stop the rest
            if on_error:
                on_error(f"{source_label(name)}: {exc}")

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
    return _DOWNLOADERS.get(candidate.source, _download_direct)(candidate)


def fetch_preview(url: str) -> bytes | None:
    """Fetch a small preview image; returns None instead of raising."""
    try:
        resp = _session.get(url, timeout=15, allow_redirects=True)
        if resp.status_code == 200 and resp.content:
            return resp.content
    except requests.RequestException:
        pass
    return None


def check_spotify_credentials(client_id: str, client_secret: str) -> None:
    """Raise SourceError if the credentials are not usable. Used by Settings."""
    _spotify_token.update({"expires": 0.0})
    _spotify_access_token(client_id.strip(), client_secret.strip())


def image_dimensions(data: bytes) -> tuple[int, int]:
    from PIL import Image
    with Image.open(io.BytesIO(data)) as img:
        return img.size
