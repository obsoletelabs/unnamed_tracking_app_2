"""On-disk trash for whole games — the highest-blast-radius delete in the
app (cascades to screenshots, clips, notes, achievements, saves,
everything). Deleting moves the entire game folder aside instead of
removing it, so features/trash/sweep.py has something to restore from for
7 days.

Moving and restoring never remove existing data. A destination that already
exists (a leftover from an interrupted move, a folder recreated by hand, a
repeated delete) is merged with: the moving side keeps its names, a
colliding destination entry is renamed to ``<name> (preserved-N)<ext>``
beside it (files and directories alike, including a file facing a
directory), and a byte-identical file is not duplicated. Every step is
idempotent, so a move interrupted at any point can simply be run again.

Recovery states (the row is always locked exclusively while storage moves):

* row active, data in ``<data>/_trash/<game-id>``: a delete whose commit or
  move was interrupted. The failure path moves it back; the sweep does the
  same on its next pass if the process died first; a repeated delete simply
  finishes it.
* row deleted, data in the owned active folder: a restore whose commit was
  interrupted. The failure path moves it back to trash; a repeated restore
  finishes it. The sweep only reports it and never purges the active folder.
"""

from __future__ import annotations

import asyncio
import errno
import filecmp
import logging
import os
import shutil
import time
import uuid
from pathlib import Path
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models.game import Game
from src.features import game_folders
from src.helpers.async_storage import finish_storage_operation

logger = logging.getLogger(__name__)

PRESERVED_LABEL = "preserved"
_MAX_PRESERVED_SUFFIXES = 10_000


def game_trash_dir(data_root: Path, game_id: UUID) -> Path:
    return data_root / "_trash" / str(game_id)


def _is_real_dir(path: Path) -> bool:
    return path.is_dir() and not path.is_symlink()


def _exists(path: Path) -> bool:
    return os.path.lexists(path)


def _preserved_sibling(path: Path, *, is_dir: bool) -> Path:
    """A free ``<stem> (preserved-N)<ext>`` name beside ``path``; taken names are skipped."""
    suffix = "" if is_dir else path.suffix
    stem = path.name[: len(path.name) - len(suffix)] if suffix else path.name
    for number in range(1, _MAX_PRESERVED_SUFFIXES):
        candidate = path.with_name(f"{stem} ({PRESERVED_LABEL}-{number}){suffix}")
        if not _exists(candidate):
            return candidate
    raise game_folders.FolderStorageError(
        f"Too many preserved copies of {path.name}; nothing was removed."
    )


def _same_bytes(first: Path, second: Path) -> bool:
    if first.is_symlink() or second.is_symlink():
        return False
    if not (first.is_file() and second.is_file()):
        return False
    return filecmp.cmp(first, second, shallow=False)


def _copy_across_devices(source: Path, dest: Path) -> None:
    """Publish a copy atomically, then drop the source; a crash leaves two whole copies."""
    partial = dest.with_name(f".{dest.name}.partial-{uuid.uuid4().hex}")
    try:
        if source.is_symlink():
            os.symlink(os.readlink(source), partial)
        else:
            shutil.copy2(source, partial)
        os.rename(partial, dest)
    except BaseException:
        if _exists(partial):
            partial.unlink()
        raise
    source.unlink()


def _place(source: Path, dest: Path) -> None:
    """Move ``source`` to the free name ``dest`` without ever replacing anything."""
    if _exists(dest):
        raise game_folders.FolderStorageError(f"{dest.name} appeared during the move.")
    try:
        os.rename(source, dest)
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            raise
        if _is_real_dir(source):
            dest.mkdir()
            _merge_into(source, dest)
        else:
            _copy_across_devices(source, dest)


def _merge_into(source_dir: Path, dest_dir: Path) -> None:
    """Move every entry of ``source_dir`` into the existing ``dest_dir``, then drop the empty source."""
    for entry in sorted(os.scandir(source_dir), key=lambda item: item.name):
        source = Path(entry.path)
        dest = dest_dir / entry.name
        source_is_dir = entry.is_dir(follow_symlinks=False)
        if not _exists(dest):
            _place(source, dest)
            continue
        dest_is_dir = _is_real_dir(dest)
        if source_is_dir and dest_is_dir:
            _merge_into(source, dest)
        elif _same_bytes(source, dest):
            source.unlink()
        else:
            aside = _preserved_sibling(dest, is_dir=dest_is_dir)
            logger.warning(
                "Kept both copies of %s in %s; the earlier one is now %s.",
                entry.name,
                dest_dir,
                aside.name,
            )
            os.rename(dest, aside)
            _place(source, dest)
    source_dir.rmdir()


def _merge_directory(source: Path, dest: Path) -> None:
    if source.is_symlink() or not source.is_dir():
        raise game_folders.FolderStorageError("Game storage must be a directory without symlinks.")
    if _exists(dest):
        if not _is_real_dir(dest):
            raise game_folders.FolderStorageError(
                "The destination is not a plain directory; both locations were preserved."
            )
        _merge_into(source, dest)
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    _place(source, dest)


def _checked_trash_dir(data_root: Path, game_id: UUID) -> Path:
    trash_root = data_root / "_trash"
    if trash_root.is_symlink() or (trash_root.exists() and not trash_root.is_dir()):
        raise game_folders.FolderStorageError("Trash storage must be a plain directory.")
    return game_trash_dir(data_root, game_id)


def move_game_to_trash(active_dir: Path, data_root: Path, game_id: UUID) -> None:
    """Move the active folder under trash, merging with whatever is already there."""
    if not _exists(active_dir):
        return
    _merge_directory(active_dir, _checked_trash_dir(data_root, game_id))


def restore_game_from_trash(active_dir: Path, data_root: Path, game_id: UUID) -> None:
    """Move the trashed folder back, merging with whatever is already at ``active_dir``."""
    source = _checked_trash_dir(data_root, game_id)
    if not _exists(source):
        return
    _merge_directory(source, active_dir)


def purge_game_trash(data_root: Path, game_id: UUID) -> None:
    dest = game_trash_dir(data_root, game_id)
    if dest.is_dir():
        shutil.rmtree(dest, ignore_errors=True)


async def _current_row(db: AsyncSession, game_id: UUID) -> Game | None:
    return await db.scalar(
        select(Game)
        .where(Game.id == game_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


async def _settle(db: AsyncSession, game_id: UUID, data_root: Path, *, deleted: bool) -> None:
    """After a failed commit, put the files where the committed row says they belong.

    A lost commit acknowledgement may mean the change really committed, so the row is
    re-read under a fresh exclusive lock before any file is moved back. A failure here is
    logged, never raised over the original error; the data stays at one of the two locations
    for the sweep, a repeated delete or a repeated restore to finish.
    """
    try:
        current = await _current_row(db, game_id)
        if current is None or not current.folder_location:
            return
        active = game_folders.owned_folder(data_root, current.user_id, current.folder_location)
        if deleted and current.deleted_at is None:
            await asyncio.to_thread(restore_game_from_trash, active, data_root, game_id)
        elif not deleted and current.deleted_at is not None:
            await asyncio.to_thread(move_game_to_trash, active, data_root, game_id)
    except Exception:  # pylint: disable=broad-exception-caught  # best effort; a later pass retries
        logger.exception("Could not undo the interrupted move of game %s; data was kept", game_id)
    finally:
        await db.rollback()


async def _set_deleted(db: AsyncSession, game: Game, data_root: Path, *, delete: bool) -> None:
    game_id, user_id, folder = game.id, game.user_id, game.folder_location
    try:
        if folder:
            active = game_folders.owned_folder(data_root, user_id, folder)
            if delete:
                await asyncio.to_thread(move_game_to_trash, active, data_root, game_id)
            else:
                await asyncio.to_thread(restore_game_from_trash, active, data_root, game_id)
        game.deleted_at = int(time.time()) if delete else None
        await db.commit()
    except Exception:
        await db.rollback()
        await _settle(db, game_id, data_root, deleted=delete)
        raise


async def trash_game(db: AsyncSession, game: Game, data_root: Path) -> None:
    """Move the folder aside and commit the deletion, holding the caller's row lock throughout."""
    await finish_storage_operation(_set_deleted(db, game, data_root, delete=True))


async def restore_game(db: AsyncSession, game: Game, data_root: Path) -> None:
    """Move the folder back and commit the restore, holding the caller's row lock throughout."""
    await finish_storage_operation(_set_deleted(db, game, data_root, delete=False))


def _trashed_game_ids(data_root: Path) -> list[UUID]:
    trash_root = data_root / "_trash"
    if trash_root.is_symlink() or not trash_root.is_dir():
        return []
    ids = []
    for entry in os.scandir(trash_root):
        try:
            ids.append(UUID(entry.name))
        except ValueError:
            continue  # archive/media trash and anything else is not a whole-game folder
    return ids


async def recover_interrupted_deletes(db: AsyncSession, data_root: Path) -> int:
    """Move back the folder of every still-active game that was left in whole-game trash.

    An active row never legitimately has a whole-game trash folder: it only remains when a
    delete died between its move and its commit. Each game is handled under its own
    exclusive row lock, so an in-flight delete finishes (and the row turns deleted) first.
    """
    root = data_root.resolve()
    recovered = 0
    for game_id in await asyncio.to_thread(_trashed_game_ids, root):
        try:
            game = await _current_row(db, game_id)
            if game is not None and game.deleted_at is None and game.folder_location:
                game_folders.ensure_storage_ready(root, game)
                active = game_folders.owned_folder(root, game.user_id, game.folder_location)
                await asyncio.to_thread(restore_game_from_trash, active, root, game_id)
                recovered += 1
                logger.warning("Recovered game %s from an interrupted delete.", game_id)
            await db.commit()
        except (game_folders.FolderStorageError, OSError) as exc:
            await db.rollback()
            logger.warning("Kept trashed folder of game %s for later: %s", game_id, exc)
    return recovered
