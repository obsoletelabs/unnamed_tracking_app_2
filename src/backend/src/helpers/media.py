"""Shared helpers for bulk screenshot/clip uploads — used both for a game's
own screenshots/clips folders and for the per-user "inbox" of unassigned
media (uploaded before being sorted into a game)."""

from __future__ import annotations

import io
import re
import uuid
from pathlib import Path
from typing import Literal

from PIL import Image, UnidentifiedImageError

from src.helpers.remote_images import MAX_PIXELS

MediaKind = Literal["screenshot", "clip", "soundtrack"]

_SAFE_NAME = re.compile(r"[^A-Za-z0-9_.-]+")
_IMAGE_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".jfif",
    ".gif",
    ".webp",
    ".avif",
    ".bmp",
    ".tif",
    ".tiff",
    ".heic",
    ".heif",
    ".ico",
}
_VIDEO_EXTENSIONS = {".mp4", ".m4v", ".webm", ".mov", ".mkv", ".avi", ".ogv"}
_AUDIO_EXTENSIONS = {".mp3", ".ogg", ".oga", ".opus", ".wav", ".flac", ".m4a", ".aac"}
_MEDIA_TYPES: tuple[tuple[str, set[str], MediaKind], ...] = (
    ("image/", _IMAGE_EXTENSIONS, "screenshot"),
    ("video/", _VIDEO_EXTENSIONS, "clip"),
    ("audio/", _AUDIO_EXTENSIONS, "soundtrack"),
)


def classify_media(content_type: str | None, filename: str) -> MediaKind | None:
    """An allowed extension decides the kind; sender MIME cannot permit HTML/SVG."""
    del content_type
    ext = Path(original_filename(filename)).suffix.lower()
    for _, extensions, kind in _MEDIA_TYPES:
        if ext in extensions:
            return kind
    return None


_IMAGE_FORMATS = {
    ".png": "PNG",
    ".jpg": "JPEG",
    ".jpeg": "JPEG",
    ".jfif": "JPEG",
    ".gif": "GIF",
    ".webp": "WEBP",
    ".avif": "AVIF",
    ".bmp": "BMP",
    ".tif": "TIFF",
    ".tiff": "TIFF",
    ".heic": "HEIF",
    ".heif": "HEIF",
    ".ico": "ICO",
}


def _validate_picture(data: bytes, extension: str) -> None:
    try:
        with Image.open(io.BytesIO(data)) as picture:
            if picture.format != _IMAGE_FORMATS[extension]:
                raise ValueError("Image content does not match its filename extension.")
            if picture.width * picture.height > MAX_PIXELS:
                raise ValueError("Image exceeds the pixel limit.")
            picture.verify()
        # verify checks container integrity; loading also detects truncated pixel data.
        with Image.open(io.BytesIO(data)) as picture:
            picture.load()
    except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombError) as exc:
        raise ValueError("Invalid image content.") from exc


def _after_id3(data: bytes) -> bytes:
    if not data.startswith(b"ID3"):
        return data[:64]
    if len(data) < 10 or any(value >= 128 for value in data[6:10]):
        return b""
    length = sum(value << shift for value, shift in zip(data[6:10], (21, 14, 7, 0), strict=True))
    footer = 10 if data[3] == 4 and data[5] & 0x10 else 0
    start = 10 + length + footer
    return data[start : start + 64]


def _matches_audio(data: bytes, extension: str) -> bool:
    """Check audio frame/stream headers after an optional ID3 metadata tag."""
    audio = _after_id3(data)
    if extension == ".flac":
        return len(audio) >= 42 and audio.startswith(b"fLaC") and audio[4] & 0x7F == 0
    if extension == ".mp3":
        return len(audio) >= 4 and all(
            (
                audio[0] == 0xFF,
                audio[1] & 0xE0 == 0xE0,
                audio[1] & 0x18 != 0x08,
                audio[1] & 0x06 != 0,
                audio[2] & 0xF0 not in {0, 0xF0},
                audio[2] & 0x0C != 0x0C,
            )
        )
    if extension == ".aac":
        return len(audio) >= 7 and audio[0] == 0xFF and audio[1] & 0xF6 == 0xF0
    return False


def _matches_container(data: bytes, extension: str) -> bool:
    """Check known media containers/frame headers, without decoding or transcoding."""
    if extension in {".mp4", ".m4v", ".mov", ".m4a"}:
        if len(data) < 16 or data[4:8] not in {b"ftyp", b"moov", b"mdat", b"wide"}:
            return False
        length = int.from_bytes(data[:4], "big")
        if length == 1:
            length = int.from_bytes(data[8:16], "big")
        return length == 0 or 8 <= length <= len(data)
    if extension in {".avi", ".wav"}:
        form = b"AVI " if extension == ".avi" else b"WAVE"
        return len(data) >= 12 and data[:4] in {b"RIFF", b"RF64"} and data[8:12] == form
    if extension in {".webm", ".mkv"}:
        return len(data) >= 8 and data.startswith(b"\x1a\x45\xdf\xa3")
    if extension in {".ogg", ".oga", ".opus", ".ogv"}:
        if len(data) < 28 or not data.startswith(b"OggS\x00"):
            return False
        header_end = 27 + data[26]
        matches = header_end < len(data) and 0 < sum(data[27:header_end]) <= len(data) - header_end
    else:
        matches = _matches_audio(data, extension)
    return matches


def validate_media(data: bytes, filename: str, kind: MediaKind) -> None:
    """Inspect actual content before an allowed file is persisted."""
    if classify_media(None, filename) != kind:
        raise ValueError("Unsupported file extension.")
    extension = Path(original_filename(filename)).suffix.lower()
    if kind == "screenshot":
        _validate_picture(data, extension)
    elif not _matches_container(data, extension):
        raise ValueError("File content does not match a supported media container.")


_SUBDIRS: dict[MediaKind, str] = {
    "screenshot": "screenshots",
    "clip": "clips",
    "soundtrack": "soundtrack",
}


def media_subdir(kind: MediaKind) -> str:
    return _SUBDIRS[kind]


def original_filename(original_name: str) -> str:
    """Keep the Unicode basename for display/download, never client paths or controls."""
    name = original_name.replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(char for char in name if ord(char) >= 32 and ord(char) != 127).strip()
    if name in {"", ".", ".."}:
        return "file"
    if len(name) > 500:
        suffix = Path(name).suffix[:32]
        name = name[: 500 - len(suffix)] + suffix
    return name


def display_filename(stored_name: str, original_name: str | None = None) -> str:
    """Use preserved names, stripping only known storage IDs on legacy files."""
    return original_name or re.sub(r"^(?:[A-Fa-f0-9]{8}|[A-Fa-f0-9]{32})_", "", stored_name)


def safe_filename(original_name: str) -> str:
    """Strip path separators and unsafe characters; prefix a random id
    so two uploads with the same original name never collide."""
    name = original_filename(original_name)
    name = _SAFE_NAME.sub("_", name) or "file"
    suffix = Path(name).suffix
    extension = suffix[:32]
    stem = name[: -len(suffix)] if suffix else name
    return f"{uuid.uuid4().hex}_{stem[: 207 - len(extension)]}{extension}"


def save_media_bytes(data: bytes, dest_dir: Path, original_name: str) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    filename = safe_filename(original_name)
    path = dest_dir / filename
    path.write_bytes(data)
    return path


def list_media(dir_path: Path) -> list[str]:
    if not dir_path.exists():
        return []
    return sorted(p.name for p in dir_path.iterdir() if p.is_file())
