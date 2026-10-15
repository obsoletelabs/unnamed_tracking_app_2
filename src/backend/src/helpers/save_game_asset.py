from __future__ import annotations

import asyncio
from io import BytesIO
from pathlib import Path
from typing import Literal
from uuid import UUID

from PIL import Image
from sqlalchemy import select

from src.database.models.game import Game
from src.database.session import SessionLocal
from src.features import game_folders
from src.helpers.asset_previews import make_standard_previews
from src.helpers.async_storage import finish_storage_operation

DATA_ROOT = Path("/data/users")

AssetKind = Literal["key_art", "banner", "logo", "icon"]

ASSET_SIZES: dict[AssetKind, tuple[int, int]] = {
    "key_art": (600, 900),
    "banner": (3840, 1240),
    "logo": (1024, 1024),
    "icon": (512, 512),
}

ASSET_FILENAMES: dict[AssetKind, str] = {
    "key_art": "key_art.png",
    "banner": "banner.png",
    "logo": "logo.png",
    "icon": "icon.png",
}


def create_game_folder(user_id: UUID | str, folder_name: str) -> Path:
    """Create and return the persistent data directory for a game."""
    game_folders.ensure_name_available(DATA_ROOT, user_id, folder_name)
    folder_path = game_folders.owned_folder(DATA_ROOT, user_id, folder_name)
    folder_path.mkdir(parents=True, exist_ok=True)
    (folder_path / "notes").mkdir(exist_ok=True)
    (folder_path / "screenshots").mkdir(exist_ok=True)
    (folder_path / "clips").mkdir(exist_ok=True)
    return folder_path


def _load_image_bytes(image: bytes | str | Path | Image.Image) -> Image.Image:
    if isinstance(image, Image.Image):
        return image.convert("RGBA")

    if isinstance(image, (str, Path)):
        with Image.open(image) as img:
            return img.convert("RGBA")

    with Image.open(BytesIO(image)) as img:
        return img.convert("RGBA")


# stretches to the exact target box rather than preserving aspect ratio —
# deliberate, not a "contain" resize: every asset of a given kind must be
# the same pixel dimensions for consistent layout, and provider-sourced
# art (IGDB/Steam/SteamGridDB) already comes in each kind's expected ratio,
# so the stretch is negligible in practice
def _resize_to_exact(image: Image.Image, width: int, height: int) -> Image.Image:
    original = image.convert("RGBA")
    resized = original.resize((width, height), Image.Resampling.LANCZOS)
    return resized


async def save_game_asset(
    image: bytes | str | Path | Image.Image,
    game_id: UUID | str,
    asset_kind: AssetKind,
) -> Path:
    """Keep the game row locked until its artwork and previews have been saved.

    Callers already holding a game row lock must use ``save_locked_game_asset``
    instead, so an independent session never waits on the caller's transaction.
    """
    if asset_kind not in ASSET_SIZES:
        raise ValueError(f"Unsupported asset kind: {asset_kind}")
    async with SessionLocal() as session:
        game = await session.scalar(
            select(Game)
            .where(Game.id == str(game_id), Game.deleted_at.is_(None))
            .with_for_update(read=True)
        )
        if game is None:
            raise ValueError(f"Game not found for id: {game_id}")
        return await save_locked_game_asset(image, game, asset_kind)


async def save_locked_game_asset(
    image: bytes | str | Path | Image.Image,
    game: Game,
    asset_kind: AssetKind,
) -> Path:
    """Write within the caller's existing game lock or uncommitted game creation.

    Imports/enrichment keep their transaction open through this call. Using their
    current row also supports newly flushed games invisible to other sessions.
    Both entry points share this one image/preview implementation.
    """
    if asset_kind not in ASSET_SIZES:
        raise ValueError(f"Unsupported asset kind: {asset_kind}")
    if game.deleted_at is not None:
        raise ValueError(f"Game not found for id: {game.id}")
    if not game.folder_location:
        raise ValueError(f"Game {game.id} is missing folder_location")
    game_folders.ensure_storage_ready(DATA_ROOT, game)

    output_dir = create_game_folder(game.user_id, game.folder_location)

    width, height = ASSET_SIZES[asset_kind]
    image_obj = _load_image_bytes(image)
    resized = _resize_to_exact(image_obj, width, height)

    output_path = output_dir / ASSET_FILENAMES[asset_kind]
    resized.save(output_path, format="PNG")
    # the smaller copies the pages show, made now so no first visit waits for them
    await _finish_previews(output_path, asset_kind, str(game.id))
    return output_path


async def _finish_previews(output_path: Path, asset_kind: AssetKind, game_id: str) -> None:
    """Cancellation must not release storage while the thread still uses its path."""
    await finish_storage_operation(
        asyncio.to_thread(make_standard_previews, output_path, asset_kind, game_id)
    )
