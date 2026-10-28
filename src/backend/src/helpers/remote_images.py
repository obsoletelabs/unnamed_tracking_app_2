"""Keeping a local copy of a poster or backdrop that lives on another server.

Movies, shows and anime store the address of their artwork on TMDB or AniList.
This downloads each one once, shrinks it to what a page shows, and keeps it on
disk, so after the first time nothing waits on someone else's server.

The address is whatever was saved on the title, so it is checked before it is
fetched: only http(s), only public hosts (never this machine or the local
network), checked redirects, and byte/pixel limits. Connections use only the
validated DNS addresses and keep normal TLS hostname verification.
"""

import hashlib
import io
import os
import tempfile
from dataclasses import dataclass, field
from http.client import HTTPException
from pathlib import Path
from threading import Lock
from urllib.parse import quote, urljoin

from PIL import Image, UnidentifiedImageError

from src.core.cache_metrics import cache_refresh, cache_request, upstream_bytes
from src.helpers.public_http import parse_http_url, public_addresses, public_connection

MAX_BYTES = 15 * 1024 * 1024
MAX_PIXELS = 40_000_000
MAX_REDIRECTS = 3
_TIMEOUT = (5, 15)  # connect, read
_QUALITY = 82
_BACKDROP = (26, 26, 26)  # what a see-through image is laid over


class RemoteImageError(Exception):
    """The image could not be fetched or is not one we will keep."""


@dataclass
class _PendingDownload:
    lock: Lock = field(default_factory=Lock)
    users: int = 0
    error: Exception | None = None


_pending_guard = Lock()
_pending_downloads: dict[Path, _PendingDownload] = {}


def fetch_if_missing(url: str, target: Path, width: int) -> None:
    """Share concurrent cache misses, including their failure, without changing force refresh."""
    with _pending_guard:
        pending = _pending_downloads.setdefault(target, _PendingDownload())
        pending.users += 1
    try:
        with pending.lock:
            if pending.error is not None:
                cache_request("media_image", "miss")
                raise pending.error
            if not target.is_file():
                cache_request("media_image", "miss")
                try:
                    with cache_refresh("media_image"):
                        fetch_and_store(url, target, width)
                except Exception as exc:
                    pending.error = exc
                    raise
            else:
                cache_request("media_image", "hit")
    finally:
        with _pending_guard:
            pending.users -= 1
            if pending.users == 0:
                _pending_downloads.pop(target, None)


def is_public_host(host: str) -> bool:
    """True only if every resolved IPv4/IPv6 address is public."""
    try:
        public_addresses(host, 443)
    except (OSError, ValueError):
        return False
    return True


def _validate_image(data: bytes) -> None:
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.width * image.height > MAX_PIXELS:
                raise RemoteImageError("image dimensions exceed the pixel limit")
            image.verify()
    except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombError) as exc:
        raise RemoteImageError("not a valid image") from exc


def download_image(
    url: str, *, max_bytes: int = MAX_BYTES, cache_kind: str = "media_image"
) -> bytes:
    """Read a bounded raster image with pinned public DNS and checked redirects."""
    try:
        for _ in range(MAX_REDIRECTS + 1):
            parts = parse_http_url(url, allowed_ports={80, 443})
            connection = public_connection(url, timeout=_TIMEOUT[1], connect_timeout=_TIMEOUT[0])
            try:
                path = quote(parts.path or "/", safe="/%:@")
                if parts.query:
                    path += "?" + quote(parts.query, safe="%/?&=:+,;@")
                connection.request(
                    "GET",
                    path,
                    headers={
                        "User-Agent": "unnamed-tracking-app/1.0 (image cache)",
                        "Accept": "image/*",
                        "Accept-Encoding": "identity",
                    },
                )
                response = connection.getresponse()
                if response.status in {301, 302, 303, 307, 308}:
                    location = response.getheader("location")
                    if not location:
                        raise RemoteImageError("redirect has no destination")
                    if any(
                        char.isspace() or ord(char) < 32 or ord(char) == 127 for char in location
                    ):
                        raise RemoteImageError("invalid redirect destination")
                    url = urljoin(url, location)
                    continue
                if response.status != 200:
                    raise RemoteImageError(f"status {response.status}")
                declared = response.getheader("content-length")
                if declared and declared.isdigit() and int(declared) > max_bytes:
                    raise RemoteImageError("too large")
                data = bytearray()
                while chunk := response.read(min(64 * 1024, max_bytes - len(data) + 1)):
                    upstream_bytes(cache_kind, len(chunk))
                    data.extend(chunk)
                    if len(data) > max_bytes:
                        raise RemoteImageError("too large")
                result = bytes(data)
                _validate_image(result)
                return result
            finally:
                connection.close()
        raise RemoteImageError("too many image redirects")
    except (OSError, ValueError, HTTPException) as exc:
        raise RemoteImageError(str(exc)) from exc


def save_shrunk(data: bytes, target: Path, width: int) -> None:
    """Write `data` as a JPEG at most `width` pixels wide to `target`."""
    try:
        with Image.open(io.BytesIO(data)) as opened:
            has_alpha = "A" in opened.getbands() or "transparency" in opened.info
            image = opened.convert("RGBA" if has_alpha else "RGB")
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise RemoteImageError("not an image") from exc
    if image.width > width:
        height = max(1, round(image.height * width / image.width))
        image = image.resize((width, height), Image.Resampling.LANCZOS)
    if has_alpha:
        flat = Image.new("RGB", image.size, _BACKDROP)
        flat.paste(image, mask=image.getchannel("A"))
        image = flat
    target.parent.mkdir(parents=True, exist_ok=True)
    # written beside the target then moved into place, so a request that
    # arrives mid-write never reads half a file
    handle, temp_name = tempfile.mkstemp(dir=target.parent, suffix=".tmp")
    os.close(handle)
    try:
        image.save(temp_name, format="JPEG", quality=_QUALITY)
        os.replace(temp_name, target)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def cache_path(cache_root: Path, media_type: str, item_id: str, which: str, url: str) -> Path:
    """Where the local copy of one title's poster or backdrop lives. The address
    is part of the name, so changing it on the title makes a fresh copy."""
    digest = hashlib.sha1(url.encode("utf-8"), usedforsecurity=False).hexdigest()[:10]
    return cache_root / "media-images" / media_type / f"{item_id}-{which}-{digest}.jpg"


def fetch_and_store(url: str, target: Path, width: int) -> None:
    """Download `url`, keep it at `target`, and drop older copies of the same
    picture (from an address the title used to have)."""
    save_shrunk(download_image(url), target, width)
    prefix = target.name.rsplit("-", 1)[0] + "-"
    for old in target.parent.glob(f"{prefix}*.jpg"):
        if old != target:
            old.unlink(missing_ok=True)


def icon_cache_path(cache_root: Path, url: str) -> Path:
    """Where the local copy of an achievement icon lives. Icons are the same for
    every player, so they are kept once, named by their address."""
    digest = hashlib.sha1(url.encode("utf-8"), usedforsecurity=False).hexdigest()
    return cache_root / "achievement-icons" / f"{digest}.jpg"
