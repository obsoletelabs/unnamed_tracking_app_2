"""Reversible ownership groups preserve the original game and import identities."""

import hashlib
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models.game import Game

OWNED_COPY = "owned_copy"


async def lock_changes(db: AsyncSession, user_id: UUID) -> None:
    """Serialize this user's relationship edits before locking any game rows."""
    key = int.from_bytes(
        hashlib.sha256(f"game-ownership:{user_id}".encode()).digest()[:8], "big", signed=True
    )
    await db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})


async def validate_relationship(
    db: AsyncSession,
    user_id: UUID,
    parent: Game,
    relationship_type: str | None,
    game_id: UUID | None,
) -> None:
    """A group has a standalone main and one level of explicitly chosen copies."""
    if game_id is not None:
        owns_copies = await db.scalar(
            select(Game.id)
            .where(
                Game.user_id == user_id,
                Game.parent_game_id == game_id,
                Game.relationship_type == OWNED_COPY,
            )
            .limit(1)
        )
        if owns_copies is not None:
            raise ValueError("Separate this game's owned copies before making it a child.")
    if relationship_type == OWNED_COPY and parent.parent_game_id is not None:
        raise ValueError("Choose a standalone main game, not another copy or variant.")


async def group_copy(db: AsyncSession, user_id: UUID, main_id: UUID, copy_id: UUID) -> None:
    """Group explicitly; never merge fields, files, achievements or provider bindings."""
    await lock_changes(db, user_id)
    rows = {
        game.id: game
        for game in await db.scalars(
            select(Game)
            .where(
                Game.id.in_((main_id, copy_id)),
                Game.user_id == user_id,
                Game.deleted_at.is_(None),
            )
            .order_by(Game.id)
            .with_for_update()
        )
    }
    if len(rows) != 2:
        raise LookupError("Choose two different active games from your library.")
    main, copy = rows[main_id], rows[copy_id]
    if copy.parent_game_id is not None and (
        copy.parent_game_id != main_id or copy.relationship_type != OWNED_COPY
    ):
        raise ValueError("Separate this copy from its current parent before regrouping it.")
    await validate_relationship(db, user_id, main, OWNED_COPY, copy_id)
    copy.parent_game_id, copy.relationship_type = main_id, OWNED_COPY
    await db.commit()


async def separate_copy(db: AsyncSession, user_id: UUID, main_id: UUID, copy_id: UUID) -> None:
    """An expected parent prevents a stale request from separating a newer group."""
    await lock_changes(db, user_id)
    copy = await db.scalar(
        select(Game)
        .where(Game.id == copy_id, Game.user_id == user_id, Game.deleted_at.is_(None))
        .with_for_update()
    )
    if copy is None:
        raise LookupError("This copy is not an active game in your library.")
    if copy.parent_game_id is None and copy.relationship_type is None:
        return
    if copy.parent_game_id != main_id or copy.relationship_type != OWNED_COPY:
        raise ValueError("This copy's group has changed. Reload before separating it.")
    copy.parent_game_id, copy.relationship_type = None, None
    await db.commit()
