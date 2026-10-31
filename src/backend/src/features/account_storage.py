"""Remove a deleted account's own files, and nothing that belongs to anyone else (issue #471).

An account owns files under two roots, each in a directory named after its id:

* the profile root (``profile_pictures``' root, ``/data/user`` by default): the profile
  picture and the media inbox;
* the game root (``save_game_asset.DATA_ROOT``, ``/data/users`` by default): every game
  folder (artwork, notes, screenshots, clips, saves, files and per-game trash).

Deleted games also leave ``<game root>/_trash/<game id>``. That directory is keyed by
the game, so the caller passes the ids of the account's games, read before the database
rows disappear.

The roots are the ones the cleanup helpers expose (default /data/user and /data/users); the
other writers keep equal constants, which a test pins so they cannot drift apart.

Cleanup is best effort and idempotent: a missing directory counts as done, a failure on
one path never stops the others, and a second run removes whatever is left. Paths are
built only from a parsed UUID, are never resolved through a symbolic link, and a symlink
at the owned location is unlinked rather than followed.
"""

from __future__ import annotations

import logging
import os
import shutil
import stat
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID

from starlette.concurrency import run_in_threadpool

from src.features import profile_pictures
from src.helpers import save_game_asset
from src.helpers.async_storage import finish_storage_operation

logger = logging.getLogger(__name__)


@dataclass
class StorageCleanup:
    removed: list[str] = field(default_factory=list)
    absent: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)

    @property
    def complete(self) -> bool:
        return not self.failed


def _owned_path(root: Path, name: UUID) -> Path:
    """The direct child of ``root`` named for ``name``; never a path that leaves the root."""
    path = root / str(name)
    if path.parent != root or path.name != str(name):
        raise ValueError("Owned storage must be a direct child of its root.")
    return path


def _remove(root: Path, name: UUID, label: str, result: StorageCleanup) -> None:
    try:
        if root.is_symlink():
            # A redirected root could point at another user's data; leave it for the operator.
            raise OSError("storage root is a symbolic link")
        path = _owned_path(root, name)
        try:
            mode = path.lstat().st_mode
        except FileNotFoundError:
            result.absent.append(label)
            return
        if stat.S_ISDIR(mode):
            _rmtree(path)
        else:
            path.unlink()  # a file or a symlink at the owned name: remove the name only
        result.removed.append(label)
    except (OSError, ValueError) as exc:
        logger.error("Account storage cleanup failed for %s: %s", label, exc)
        result.failed[label] = exc.__class__.__name__


def _rmtree(path: Path) -> None:
    errors: list[OSError] = []

    def collect(_function, failed_path, exc):
        if isinstance(exc, FileNotFoundError):
            return
        errors.append(exc if isinstance(exc, OSError) else OSError(str(exc)))
        logger.error("Could not remove %s: %s", failed_path, exc)

    # shutil.rmtree never follows symbolic links inside the tree.
    shutil.rmtree(path, onexc=collect)
    if errors:
        raise errors[0]


def cleanup_account_storage_sync(user_id: UUID, game_ids: Iterable[UUID] = ()) -> StorageCleanup:
    result = StorageCleanup()
    game_root = save_game_asset.DATA_ROOT
    profile_root = profile_pictures.user_data_root()
    _remove(profile_root, user_id, "profile", result)
    _remove(game_root, user_id, "games", result)
    # Same location as game_trash.game_trash_dir(game_root, game_id).
    trash_root = game_root / "_trash"
    for game_id in sorted(set(game_ids), key=str):
        _remove(trash_root, game_id, f"game-trash:{game_id}", result)
    if result.failed:
        logger.error(
            "Account %s storage cleanup incomplete (removed=%s failed=%s); retry the deletion.",
            user_id,
            result.removed,
            sorted(result.failed),
        )
    else:
        logger.info(
            "Account %s storage removed (%s) or already absent (%s).",
            user_id,
            result.removed,
            result.absent,
        )
    return result


async def cleanup_account_storage(user_id: UUID, game_ids: Iterable[UUID] = ()) -> StorageCleanup:
    ids = list(game_ids)
    return await finish_storage_operation(
        run_in_threadpool(cleanup_account_storage_sync, user_id, ids)
    )


def storage_present(user_id: UUID, game_ids: Iterable[UUID] = ()) -> bool:
    """Whether any owned account directory (or listed game trash folder) still exists.

    Used to retry an interrupted cleanup; ``game_ids`` are the trash folders a previous
    attempt failed to remove.
    """
    roots = (profile_pictures.user_data_root(), save_game_asset.DATA_ROOT)
    if any(os.path.lexists(_owned_path(root, user_id)) for root in roots):
        return True
    trash_root = save_game_asset.DATA_ROOT / "_trash"
    return any(os.path.lexists(_owned_path(trash_root, game_id)) for game_id in game_ids)


TRASH_FAILURE_ACTION = "account.delete.storage.trash"
_TRASH_PREFIX = "game-trash:"


def failed_trash_labels(failed: Iterable[str]) -> list[UUID]:
    """Game ids behind the ``game-trash:<uuid>`` entries of a cleanup's failed paths."""
    ids: list[UUID] = []
    for label in failed:
        if label.startswith(_TRASH_PREFIX):
            ids.append(UUID(label[len(_TRASH_PREFIX) :]))
    return ids
