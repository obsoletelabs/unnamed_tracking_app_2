"""Coordinate owned game-folder moves with the transaction naming their storage."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models.game import FOLDER_NAME_MAX_LENGTH, FOLDER_NAME_PATTERN, Game
from src.helpers.async_storage import finish_storage_operation


class FolderStorageError(ValueError):
    """A folder operation needs a different name or recovery before storage can be used."""


def _folder_name(name: str) -> str:
    if (
        not isinstance(name, str)
        or len(name) > FOLDER_NAME_MAX_LENGTH
        or not re.fullmatch(FOLDER_NAME_PATTERN, name)
    ):
        raise FolderStorageError("Invalid game folder name.")
    return name


def _games_root(data_root: Path, user_id: UUID | str) -> Path:
    owner = str(UUID(str(user_id)))
    path = data_root.resolve()
    for part in (owner, "games"):
        path /= part
        if path.is_symlink() or (path.exists() and not path.is_dir()):
            raise FolderStorageError("Game storage must be a directory without symlinks.")
    return path


def owned_folder(data_root: Path, user_id: UUID | str, name: str) -> Path:
    """Keep folder operations beneath the owner, rejecting symlinked parents or roots."""
    path = _games_root(data_root, user_id) / _folder_name(name)
    if path.is_symlink() or (path.exists() and not path.is_dir()):
        raise FolderStorageError("Game storage must be a directory without symlinks.")
    return path


def _journal_dir(data_root: Path, user_id: UUID | str) -> Path:
    parent = _games_root(data_root, user_id) / ".folder-moves"
    if parent.is_symlink() or (parent.exists() and not parent.is_dir()):
        raise FolderStorageError("The folder recovery directory is not safe to use.")
    return parent


def _read_move(path: Path) -> dict[str, str]:
    try:
        if path.is_symlink() or path.stat().st_size > 2048:
            raise ValueError("Invalid recovery record")
        record = json.loads(path.read_text(encoding="utf-8"))
        if set(record) != {"game_id", "old", "new"} or str(UUID(record["game_id"])) != path.stem:
            raise ValueError("Invalid recovery record")
        _folder_name(record["old"])
        _folder_name(record["new"])
        return record
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        raise FolderStorageError(
            "A folder recovery record needs administrator attention; no files were removed."
        ) from exc


def ensure_name_available(data_root: Path, user_id: UUID | str, name: str) -> None:
    """Unfinished moves reserve both names, including after a database rollback."""
    owned_folder(data_root, user_id, name)
    if name in reserved_names(data_root, user_id):
        raise FolderStorageError(
            "This folder has an unfinished move. Retry saving the original game's folder name."
        )


def reserved_names(data_root: Path, user_id: UUID | str) -> set[str]:
    """Let import allocators choose another name without adopting unfinished storage."""
    names: set[str] = set()
    for marker in _journal_dir(data_root, user_id).glob("*.json"):
        record = _read_move(marker)
        names.update((record["old"], record["new"]))
    return names


def ensure_no_pending_move(data_root: Path, game: Game) -> None:
    """A recorded move must be reconciled before this game's storage is used or purged."""
    marker = _journal_dir(data_root, game.user_id) / f"{game.id}.json"
    if marker.exists() or marker.is_symlink():
        raise FolderStorageError(
            "This game's folder move needs recovery. Retry saving its folder name first."
        )


def ensure_storage_ready(data_root: Path, game: Game) -> None:
    """Call while holding the game row lock, before using any stored paths."""
    owned_folder(data_root, game.user_id, game.folder_location)
    ensure_no_pending_move(data_root, game)
    ensure_name_available(data_root, game.user_id, game.folder_location)


def recover_folder_move(data_root: Path, game: Game) -> None:
    """Reconcile a persisted move with the authoritative, exclusively locked database row."""
    marker = _journal_dir(data_root, game.user_id) / f"{game.id}.json"
    if not marker.exists() and not marker.is_symlink():
        return
    record = _read_move(marker)
    old = owned_folder(data_root, game.user_id, record["old"])
    new = owned_folder(data_root, game.user_id, record["new"])
    if game.folder_location == record["new"] and new.is_dir():
        marker.unlink()
        return
    if game.folder_location == record["old"]:
        if not old.exists() and new.is_dir():
            new.rename(old)
        elif not old.is_dir() or new.exists():
            raise FolderStorageError("Folder recovery is ambiguous; both locations were preserved.")
        marker.unlink()
        return
    raise FolderStorageError("Folder recovery needs administrator attention; files were preserved.")


async def _commit_move(db: AsyncSession, game: Game, old_name: str, data_root: Path) -> None:
    game_id, user_id = game.id, game.user_id
    old = owned_folder(data_root, user_id, old_name)
    new = owned_folder(data_root, user_id, game.folder_location)
    marker = _journal_dir(data_root, user_id) / f"{game_id}.json"
    try:
        # Reserve the target in the database before publishing a recovery record.
        await db.flush()
        ensure_name_available(data_root, user_id, game.folder_location)
        if new.exists():
            raise FolderStorageError("The destination folder already exists. Choose another name.")
        if old.exists():
            marker.parent.mkdir(parents=True, exist_ok=True)
            with marker.open("x", encoding="utf-8") as handle:
                json.dump(
                    {"game_id": str(game_id), "old": old_name, "new": game.folder_location},
                    handle,
                )
                handle.flush()
                os.fsync(handle.fileno())
            old.rename(new)
        await db.commit()
    except Exception:
        await db.rollback()
        # A lost commit acknowledgement may mean the new name actually committed.
        # Re-read before undoing anything; never blindly move committed files back.
        await _recover_current(db, game_id, user_id, data_root)
        raise
    if marker.exists():
        # Commit released the row lock. Reacquire it before journal cleanup,
        # which might otherwise remove a subsequent editor's recovery record.
        await _recover_current(db, game_id, user_id, data_root)


async def _recover_current(db: AsyncSession, game_id: UUID, user_id: UUID, data_root: Path) -> None:
    current = await db.scalar(
        select(Game)
        .where(Game.id == game_id, Game.user_id == user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if current is not None:
        recover_folder_move(data_root, current)


async def commit_folder_move(db: AsyncSession, game: Game, old_name: str, data_root: Path) -> None:
    """Finish commit/recovery before cancellation can release the caller's session."""
    await finish_storage_operation(_commit_move(db, game, old_name, data_root))
