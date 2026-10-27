"""Shared atomic collection collage/upload rendering for games and media."""

import hashlib
import json
import os
import tempfile
from collections.abc import Sequence
from io import BytesIO
from pathlib import Path
from uuid import UUID

from PIL import Image, ImageDraw, ImageOps, UnidentifiedImageError

from src.helpers.game_cover_placeholder import cover_palette

COVER_SIZE = (400, 600)


def _placeholder(canvas: Image.Image, seed: str, box: tuple[int, int, int, int]) -> None:
    """Match the library's stable gamepad and palette without an image request."""
    background, foreground, _accent = cover_palette(seed)
    _placeholder_gradient(canvas, background, foreground, box)
    _placeholder_gamepad(canvas, box)


def _placeholder_gradient(
    canvas: Image.Image, background: str, foreground: str, box: tuple[int, int, int, int]
) -> None:
    left, top, right, bottom = box
    draw = ImageDraw.Draw(canvas)
    height = bottom - top
    start = tuple(int(foreground[i : i + 2], 16) for i in (1, 3, 5))
    end = tuple(int(background[i : i + 2], 16) for i in (1, 3, 5))
    for y in range(top, bottom):
        fraction = (y - top) / max(1, height - 1)
        color = tuple(
            round(a * (1 - fraction) + b * fraction) for a, b in zip(start, end, strict=True)
        )
        draw.line((left, y, right, y), fill=color)


_GAMEPAD_OUTLINE = (
    (17, 24),
    (47, 24),
    (55, 30),
    (59, 48),
    (52, 54),
    (44, 46),
    (20, 46),
    (12, 54),
    (5, 48),
    (9, 30),
    (17, 24),
)


def _placeholder_gamepad(canvas: Image.Image, box: tuple[int, int, int, int]) -> None:
    draw = ImageDraw.Draw(canvas)
    scale = 0.54 * min((box[2] - box[0]) / 64, (box[3] - box[1]) / 64)
    center = ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)

    def point(x: float, y: float) -> tuple[int, int]:
        return round(center[0] + (x - 32) * scale), round(center[1] + (y - 32) * scale)

    stroke = max(2, round(3 * scale))
    draw.line(
        [point(x, y) for x, y in _GAMEPAD_OUTLINE], fill="#aeb7c2", width=stroke, joint="curve"
    )
    for line in (((19, 31), (19, 43)), ((13, 37), (25, 37))):
        draw.line((point(*line[0]), point(*line[1])), fill="#aeb7c2", width=stroke)
    for x, y in ((42, 34), (49, 39)):
        px, py = point(x, y)
        draw.ellipse((px - stroke, py - stroke, px + stroke, py + stroke), fill="#aeb7c2")


def source_signature(rows: Sequence[tuple[UUID | str, str, Path]]) -> str:
    parts = []
    for game_id, title, path in rows:
        try:
            info = path.stat()
            signature = (info.st_mtime_ns, info.st_size)
        except FileNotFoundError:
            signature = None
        parts.append((str(game_id), title, signature))
    return hashlib.sha256(json.dumps(parts).encode("utf-8")).hexdigest()


def _save_webp(image: Image.Image, destination: Path, quality: int) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=destination.parent, suffix=".tmp")
    os.close(handle)
    try:
        image.save(temporary, format="WEBP", quality=quality, method=4)
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def render_cover(
    rows: Sequence[tuple[UUID | str, str, Path]], name: str, destination: Path
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    canvas = Image.new("RGB", COVER_SIZE)
    available = [path.is_file() for _, _, path in rows]
    if not any(available):
        _placeholder(canvas, name, (0, 0, *COVER_SIZE))
    else:
        count = len(rows)
        if count == 1:
            boxes = [(0, 0, *COVER_SIZE)]
        elif count == 2:
            boxes = [(0, 0, 198, 600), (202, 0, 400, 600)]
        else:
            boxes = [(0, 0, 198, 298), (202, 0, 400, 298), (0, 302, 198, 600), (202, 302, 400, 600)]
        for (game_id, _title, path), box in zip(rows, boxes, strict=False):
            if not path.is_file():
                _placeholder(canvas, str(game_id), box)
                continue
            try:
                with Image.open(path) as source:
                    tile = ImageOps.fit(source.convert("RGB"), (box[2] - box[0], box[3] - box[1]))
                    canvas.paste(tile, box[:2])
            except (UnidentifiedImageError, OSError):
                _placeholder(canvas, str(game_id), box)
        if count == 3:
            _placeholder(canvas, name, boxes[3])
    _save_webp(canvas, destination, 82)
    canvas.close()


def save_custom_cover(data: bytes, destination: Path) -> None:
    with Image.open(BytesIO(data)) as source:
        if source.width * source.height > 25_000_000:
            raise ValueError("Collection cover is too large.")
        source.load()
        image = ImageOps.fit(ImageOps.exif_transpose(source).convert("RGB"), COVER_SIZE)
    _save_webp(image, destination, 85)
    image.close()
