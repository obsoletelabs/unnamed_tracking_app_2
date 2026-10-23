"""User-scoped game matching and folder allocation shared by library imports."""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models.game import FOLDER_NAME_MAX_LENGTH, Game, GameStatus
from src.features.achievement_sync_queue import enqueue_game as enqueue_achievement_sync
from src.features.game_folders import reserved_names
from src.features.game_identity import names
from src.helpers import save_game_asset
from src.helpers.save_game_asset import create_game_folder

_SLUG_INVALID = re.compile(r"[^A-Za-z0-9_-]+")


async def lock_imported_game(
    db: AsyncSession, user_id: UUID, game_id: UUID, source: str
) -> Game | None:
    """Re-read an active provider record before applying delayed import data."""
    return await db.scalar(
        select(Game)
        .where(
            Game.id == game_id,
            Game.user_id == user_id,
            Game.source == source,
            Game.deleted_at.is_(None),
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )


def _folder_candidates(title: str) -> Iterator[str]:
    base = (_SLUG_INVALID.sub("-", title).strip("-") or "game")[:FOLDER_NAME_MAX_LENGTH]
    yield base
    suffix = 2
    while True:
        tail = f"-{suffix}"
        yield f"{base[: FOLDER_NAME_MAX_LENGTH - len(tail)]}{tail}"
        suffix += 1


async def _unique_folder_location(db: AsyncSession, user_id: UUID, title: str) -> str:
    statement = select(Game.id).where(Game.user_id == user_id)
    reserved = reserved_names(save_game_asset.DATA_ROOT, user_id)
    for candidate in _folder_candidates(title):
        if (
            candidate not in reserved
            and await db.scalar(statement.where(Game.folder_location == candidate)) is None
        ):
            return candidate
    raise AssertionError("unreachable")


@dataclass
class LibraryIndex:
    """One user's source and folder names, valid only inside the loading transaction."""

    user_id: UUID
    source: str
    by_external: dict[str, Game] = field(default_factory=dict)
    by_title: dict[str, list[Game]] = field(default_factory=dict)
    folders: set[str] = field(default_factory=set)

    @classmethod
    async def load(cls, db: AsyncSession, user_id: UUID, source: str) -> LibraryIndex:
        index = cls(user_id, source)
        games = await db.scalars(
            select(Game)
            .where(Game.user_id == user_id, Game.source == source, Game.deleted_at.is_(None))
            .order_by(Game.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        for game in games:
            index.remember(game)
        # Trashed records retain their folder names for a future restore.
        index.folders = set(
            await db.scalars(select(Game.folder_location).where(Game.user_id == user_id))
        )
        return index

    def remember(self, game: Game, previous_title: str | None = None) -> None:
        if previous_title is not None and previous_title != game.title:
            previous = self.by_title.get(previous_title, [])
            if game in previous:
                previous.remove(game)
        if game.external_id:
            self.by_external.setdefault(game.external_id, game)
        same_title = self.by_title.setdefault(game.title, [])
        if game not in same_title:
            same_title.append(game)
        self.folders.add(game.folder_location)

    def find(self, title: str, external_id: str | None) -> Game | None:
        if external_id and external_id in self.by_external:
            return self.by_external[external_id]
        return next(
            (
                game
                for game in self.by_title.get(title, [])
                if not external_id or not game.external_id
            ),
            None,
        )

    def claim_folder(self, title: str) -> str:
        self.folders.update(reserved_names(save_game_asset.DATA_ROOT, self.user_id))
        folder = next(name for name in _folder_candidates(title) if name not in self.folders)
        self.folders.add(folder)
        return folder


async def _find(
    db: AsyncSession, user_id: UUID, title: str, source: str, external_id: str | None
) -> Game | None:
    statement = (
        select(Game)
        .where(Game.user_id == user_id, Game.source == source, Game.deleted_at.is_(None))
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if external_id:
        existing = await db.scalar(statement.where(Game.external_id == external_id))
        if existing is not None:
            return existing
        statement = statement.where(or_(Game.external_id.is_(None), Game.external_id == ""))
    return await db.scalar(statement.where(Game.title == title).order_by(Game.id).limit(1))


async def get_or_create_game(
    db: AsyncSession,
    user_id: UUID,
    title: str,
    source: str,
    external_id: str | None = None,
    *,
    index: LibraryIndex | None = None,
    identity: UUID | None = None,
) -> tuple[Game, bool]:
    """Prefer provider identity; explicit plugin identities never adopt a title match."""
    if identity is not None and index is not None:
        raise ValueError("Explicit identity cannot use a source index")
    if index is not None and (index.user_id != user_id or index.source != source):
        raise ValueError("Library index scope mismatch")
    if identity is not None:
        existing = await db.scalar(
            select(Game)
            .where(Game.id == identity, Game.user_id == user_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if existing is not None:
            # The public import handler owns deleted/conflicting identity policy.
            return existing, False
    else:
        existing = (
            index.find(title, external_id)
            if index is not None
            else await _find(db, user_id, title, source, external_id)
        )
    if existing is not None:
        previous_title = existing.title
        if previous_title != title:
            # Placeholder, lock and trust rules (#98): a later low-quality name never
            # replaces a good one and a locked name is only proposed for review.
            names.apply_candidate_title(existing, title)
        if external_id and not existing.external_id:
            existing.external_id = external_id
        existing.stale_since = None
        if index is not None:
            index.remember(existing, previous_title)
        return existing, False

    folder_location = (
        index.claim_folder(title)
        if index is not None
        else await _unique_folder_location(
            db,
            user_id,
            f"{title[: FOLDER_NAME_MAX_LENGTH - 33]}-{identity.hex}" if identity else title,
        )
    )
    game = Game(
        id=identity,
        user_id=user_id,
        title=title,
        sort_title=title.lower(),
        folder_location=folder_location,
        source=source,
        external_id=external_id,
        status=GameStatus.BACKLOG,
    )
    db.add(game)
    await db.flush()
    await enqueue_achievement_sync(db, user_id, game.id)
    if index is not None:
        index.remember(game)
    create_game_folder(user_id, folder_location)
    return game, True
