"""Background sweep that permanently purges soft-deleted archives/versions
older than the retention window. Runs once at startup and then on a fixed
interval (see main.py) — in-process, no new container, matching how world
map renders already run as a plain asyncio background task rather than a
separate worker.

Game files are found where the storage routes keep them: the owned
``<data>/users/<user-id>/games/<folder>`` directory, or
``<data>/users/_trash/<game-id>`` once the whole game is in trash. Each
expired game-file row is purged in its own transaction under the same game
row lock the routes use, and is re-checked under that lock, so a folder
move, upload, restore or second sweep either finishes first or waits.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, TypeVar
from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.core.auth import purge_expired_sessions
from src.database.models.game import Game
from src.database.models.game_archive import GameArchive, GameArchiveVersion
from src.database.models.game_checklist_item import GameChecklistItem
from src.database.models.game_file_item import GameFileItem
from src.database.models.game_profile import GameProfile
from src.database.models.inbox_item import InboxItem
from src.database.models.media_item import MediaItem
from src.database.session import SessionLocal
from src.features import game_folders
from src.features.trash.archive_trash import purge_archive_trash, trash_files_dir, trash_root
from src.features.trash.game_trash import game_trash_dir, purge_game_trash
from src.features.trash.inbox_trash import purge_inbox_file
from src.features.trash.media_titles import purge_expired_media_titles
from src.features.trash.media_trash import media_trash_dir, purge_media_file
from src.helpers import save_game_asset
from src.helpers.async_storage import finish_storage_operation

logger = logging.getLogger(__name__)

_USER_DATA_ROOT = Path("/data/user")
RETENTION_SECONDS = 7 * 24 * 60 * 60
SWEEP_INTERVAL_SECONDS = 6 * 60 * 60

# (session, row id, owning game id, cutoff) -> whether that row was purged
_PurgeRow = Callable[[AsyncSession, UUID, UUID, int], Awaitable[bool]]
_TrashedFile = TypeVar("_TrashedFile", MediaItem, GameFileItem)


def _contained(base: Path, path: Path) -> Path:
    """Reject trash paths that leave their game storage or pass through a symlink."""
    try:
        parts = path.relative_to(base).parts
    except ValueError as exc:
        raise game_folders.FolderStorageError("Trash path is outside its game storage.") from exc
    current = base
    for part in parts:
        current /= part
        if part in {".", ".."} or current.is_symlink():
            raise game_folders.FolderStorageError(
                "Trash storage must be a plain path without symlinks."
            )
    return path


def _plain_name(name: str) -> str:
    if not name or name in {".", ".."} or Path(name).name != name:
        raise game_folders.FolderStorageError("A trashed file name is ambiguous.")
    return name


def _is_expired(deleted_at: int | None, cutoff: int) -> bool:
    return deleted_at is not None and deleted_at < cutoff


def _ensure_removed(path: Path) -> None:
    # The removal helpers ignore errors; keep the row so a later pass retries.
    if path.exists() or path.is_symlink():
        raise OSError(f"Could not remove {path.name} from trash.")


def _game_storage(game: Game) -> Path:
    """Where a locked game's files are now; unfinished moves and symlinks are refused."""
    data_root = save_game_asset.DATA_ROOT
    if game.deleted_at is None:
        game_folders.ensure_storage_ready(data_root, game)
        return game_folders.owned_folder(data_root, game.user_id, game.folder_location)
    # Deleting a game moved its whole folder, nested trash included, aside by id.
    # Its old name may already belong to another game, so never resolve that.
    game_folders.ensure_no_pending_move(data_root, game)
    root = data_root.resolve()
    return _contained(root, game_trash_dir(root, game.id))


async def _lock_game(db: AsyncSession, game_id: UUID, *, exclusive: bool = False) -> Game | None:
    """The routes' shared storage lock, or their exclusive one for whole-game changes."""
    return await db.scalar(
        select(Game)
        .where(Game.id == game_id)
        .with_for_update(read=not exclusive)
        .execution_options(populate_existing=True)
    )


async def _purge_row(
    db: AsyncSession, purge: _PurgeRow, row_id: UUID, game_id: UUID, cutoff: int
) -> bool:
    try:
        purged = await purge(db, row_id, game_id, cutoff)
        await db.commit()
    except Exception:
        await db.rollback()
        raise
    return purged


async def _purge_rows(
    db: AsyncSession, label: str, candidates: Select[Any], purge: _PurgeRow, cutoff: int
) -> int:
    purged = 0
    for row_id, game_id in (await db.execute(candidates)).tuples().all():
        try:
            # Cancellation waits for this row's files and transaction to finish.
            if await finish_storage_operation(_purge_row(db, purge, row_id, game_id, cutoff)):
                purged += 1
        except (game_folders.FolderStorageError, OSError) as exc:
            logger.warning("Trash sweep kept %s %s for a later pass: %s", label, row_id, exc)
    return purged


async def _purge_archive(db: AsyncSession, archive_id: UUID, game_id: UUID, cutoff: int) -> bool:
    game = await _lock_game(db, game_id)
    archive = await db.get(
        GameArchive,
        archive_id,
        options=[selectinload(GameArchive.versions)],
        with_for_update=True,
        populate_existing=True,
    )
    if game is None or archive is None or archive.game_id != game_id:
        return False
    if not _is_expired(archive.deleted_at, cutoff):
        return False  # restored or purged while this pass was running
    game_dir = _game_storage(game)
    kind = archive.kind
    root = _contained(game_dir, trash_root(game_dir, kind, archive_id))
    await db.delete(archive)  # cascades to remaining versions
    await db.flush()
    await asyncio.to_thread(purge_archive_trash, game_dir, kind, archive_id)
    _ensure_removed(root)
    return True


async def _purge_archives(db: AsyncSession, cutoff: int) -> int:
    candidates = select(GameArchive.id, GameArchive.game_id).where(
        GameArchive.deleted_at.is_not(None), GameArchive.deleted_at < cutoff
    )
    return await _purge_rows(db, "archive", candidates, _purge_archive, cutoff)


async def _purge_version(db: AsyncSession, version_id: UUID, game_id: UUID, cutoff: int) -> bool:
    game = await _lock_game(db, game_id)
    archive_id = await db.scalar(
        select(GameArchiveVersion.archive_id).where(GameArchiveVersion.id == version_id)
    )
    if game is None or archive_id is None:
        return False
    # Archive restores reset every version while holding this archive lock.
    archive = await db.get(GameArchive, archive_id, with_for_update=True, populate_existing=True)
    version = await db.get(
        GameArchiveVersion, version_id, with_for_update=True, populate_existing=True
    )
    if archive is None or version is None or archive.game_id != game_id:
        return False
    if version.archive_id != archive.id or not _is_expired(version.deleted_at, cutoff):
        return False
    game_dir = _game_storage(game)
    # A trashed archive keeps earlier-trashed versions in this same folder, so
    # remove the file either way; restoring the archive must not revive it.
    trashed_file = _contained(
        game_dir,
        trash_files_dir(game_dir, archive.kind, archive.id) / _plain_name(version.filename),
    )
    await db.delete(version)
    await db.flush()
    await asyncio.to_thread(trashed_file.unlink, missing_ok=True)
    return True


async def _purge_versions(db: AsyncSession, cutoff: int) -> int:
    candidates = (
        select(GameArchiveVersion.id, GameArchive.game_id)
        .join(GameArchive)
        .where(GameArchiveVersion.deleted_at.is_not(None), GameArchiveVersion.deleted_at < cutoff)
    )
    return await _purge_rows(db, "archive version", candidates, _purge_version, cutoff)


async def _purge_inbox_items(db: AsyncSession, cutoff: int) -> int:
    inbox_result = await db.execute(
        select(InboxItem).where(InboxItem.deleted_at.is_not(None), InboxItem.deleted_at < cutoff)
    )
    purged_inbox_items = 0
    for inbox_item in inbox_result.scalars().all():
        inbox_dir = _USER_DATA_ROOT / str(inbox_item.user_id) / "inbox"
        purge_inbox_file(inbox_item.filename, inbox_dir, inbox_item.kind)
        await db.delete(inbox_item)
        purged_inbox_items += 1
    await db.commit()
    return purged_inbox_items


async def _purge_trashed_file(
    db: AsyncSession, model: type[_TrashedFile], item_id: UUID, game_id: UUID, cutoff: int
) -> bool:
    game = await _lock_game(db, game_id)
    item = await db.get(model, item_id, with_for_update=True, populate_existing=True)
    if game is None or item is None or item.game_id != game_id:
        return False
    if not _is_expired(item.deleted_at, cutoff):
        return False
    game_dir = _game_storage(game)
    filename, kind = _plain_name(item.filename), item.kind
    _contained(game_dir, media_trash_dir(game_dir, kind) / filename)
    await db.delete(item)
    await db.flush()
    await asyncio.to_thread(purge_media_file, filename, game_dir, kind)
    return True


async def _purge_media_item(db: AsyncSession, item_id: UUID, game_id: UUID, cutoff: int) -> bool:
    return await _purge_trashed_file(db, MediaItem, item_id, game_id, cutoff)


async def _purge_media_items(db: AsyncSession, cutoff: int) -> int:
    # Child files before games. A deleted game's own trash folder still holds
    # its children's trash, so purging them there means restoring the game
    # cannot revive expired files, and the game's old folder name (which
    # another game may have taken since) is never used.
    candidates = select(MediaItem.id, MediaItem.game_id).where(
        MediaItem.deleted_at.is_not(None), MediaItem.deleted_at < cutoff
    )
    return await _purge_rows(db, "media item", candidates, _purge_media_item, cutoff)


async def _purge_file_item(db: AsyncSession, item_id: UUID, game_id: UUID, cutoff: int) -> bool:
    return await _purge_trashed_file(db, GameFileItem, item_id, game_id, cutoff)


async def _purge_file_items(db: AsyncSession, cutoff: int) -> int:
    candidates = select(GameFileItem.id, GameFileItem.game_id).where(
        GameFileItem.deleted_at.is_not(None), GameFileItem.deleted_at < cutoff
    )
    return await _purge_rows(db, "file item", candidates, _purge_file_item, cutoff)


async def _purge_profiles(db: AsyncSession, cutoff: int) -> int:
    # profiles and checklist items are DB-only (no files to move), so
    # purging one is just deleting the row once past retention
    profile_result = await db.execute(
        select(GameProfile).where(
            GameProfile.deleted_at.is_not(None), GameProfile.deleted_at < cutoff
        )
    )
    purged_profiles = 0
    for profile in profile_result.scalars().all():
        await db.delete(profile)
        purged_profiles += 1
    await db.commit()
    return purged_profiles


async def _purge_checklist_items(db: AsyncSession, cutoff: int) -> int:
    checklist_result = await db.execute(
        select(GameChecklistItem).where(
            GameChecklistItem.deleted_at.is_not(None), GameChecklistItem.deleted_at < cutoff
        )
    )
    purged_checklist_items = 0
    for checklist_item in checklist_result.scalars().all():
        await db.delete(checklist_item)
        purged_checklist_items += 1
    await db.commit()
    return purged_checklist_items


async def _purge_game(db: AsyncSession, game_id: UUID, _game_id: UUID, cutoff: int) -> bool:
    # Restoring or deleting a game takes this exclusive lock too.
    game = await _lock_game(db, game_id, exclusive=True)
    if game is None or not _is_expired(game.deleted_at, cutoff):
        return False
    trashed_dir = _game_storage(game)
    await db.delete(game)
    await db.flush()
    await asyncio.to_thread(purge_game_trash, save_game_asset.DATA_ROOT.resolve(), game_id)
    _ensure_removed(trashed_dir)
    return True


async def _purge_games(db: AsyncSession, cutoff: int) -> int:
    # games last — hard-deleting the row cascades to any remaining
    # archives/versions/media/achievements/notes still attached, and
    # purge_game_trash removes the whole folder tree (including any
    # nested archive/media trash subfolders that hadn't hit their own
    # retention yet) in one shot
    candidates = select(Game.id, Game.id.label("game_id")).where(
        Game.deleted_at.is_not(None), Game.deleted_at < cutoff
    )
    return await _purge_rows(db, "game", candidates, _purge_game, cutoff)


async def purge_expired_trash() -> dict[str, int]:
    cutoff = int(time.time()) - RETENTION_SECONDS
    async with SessionLocal() as db:
        # Child files must be purged before their owning game's folder/cascade.
        counts = {
            "purged_archives": await _purge_archives(db, cutoff),
            "purged_versions": await _purge_versions(db, cutoff),
            "purged_inbox_items": await _purge_inbox_items(db, cutoff),
            "purged_media_items": await _purge_media_items(db, cutoff),
            "purged_file_items": await _purge_file_items(db, cutoff),
            "purged_profiles": await _purge_profiles(db, cutoff),
            "purged_checklist_items": await _purge_checklist_items(db, cutoff),
            "purged_games": await _purge_games(db, cutoff),
        }
    if any(counts.values()):
        logger.info(
            "Trash sweep purged %d game(s), %d archive(s), %d version(s), %d media item(s), "
            "%d file item(s), %d inbox item(s), %d profile(s), %d checklist item(s)",
            counts["purged_games"],
            counts["purged_archives"],
            counts["purged_versions"],
            counts["purged_media_items"],
            counts["purged_file_items"],
            counts["purged_inbox_items"],
            counts["purged_profiles"],
            counts["purged_checklist_items"],
        )
    return counts


async def run_sweep_loop() -> None:
    while True:
        try:
            await purge_expired_trash()
        # Keep the periodic cleanup boundary alive after an unexpected failed pass.
        # pylint: disable-next=broad-exception-caught
        except Exception:
            logger.exception("Trash sweep failed")
        try:
            await purge_expired_media_titles()
        # Keep media-title retention independent of game-file cleanup.
        # pylint: disable-next=broad-exception-caught
        except Exception:
            logger.exception("Media-title trash sweep failed")
        # a failed cleanup only means expired rows wait for the next sweep;
        # it never affects startup or signing in
        try:
            async with SessionLocal() as db:
                removed = await purge_expired_sessions(db)
            if removed:
                logger.info("Removed %d expired login session(s)", removed)
        # Keep the periodic cleanup boundary alive after an unexpected failed pass.
        # pylint: disable-next=broad-exception-caught
        except Exception:
            logger.exception("Expired session cleanup failed")
        await asyncio.sleep(SWEEP_INTERVAL_SECONDS)
