"""Merging two records as one copy seen through two sources.

Playnite names the library (store) of every game it finds, so a Playnite record of an Epic game
and the Epic plugin's record are one copy reported twice: playtime and last played must be
counted once, not summed. Epic and Steam copies of a game are different copies even though
they are one game, so they are never grouped this way."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models.game import Game
from src.features.game_identity import counters
from src.features.game_identity.graph import IdentityError, IdentityInvalid
from src.features.game_identity.stores import store_key


def _differ(first: str | None, second: str | None) -> bool:
    return bool(first) and bool(second) and first != second


async def group_as_same_copy(db: AsyncSession, user_id: UUID, first_id: UUID, second_id: UUID):
    """Count the pair's playtime and last played once. Raises `IdentityInvalid` when the two
    records name different stores or platforms, because those are different copies."""
    rows = (
        await db.execute(
            select(Game.id, Game.source, Game.platform).where(
                Game.user_id == user_id, Game.id.in_((first_id, second_id))
            )
        )
    ).all()
    if len(rows) != 2:
        raise LookupError("Both games must be in your library.")
    first, second = rows
    if _differ(store_key(first.source), store_key(second.source)):
        raise IdentityInvalid(
            "These come from different stores, so they are different copies of the game"
        )
    if _differ((first.platform or "").casefold(), (second.platform or "").casefold()):
        raise IdentityInvalid("These are on different platforms, so they are different copies")
    try:
        async with db.begin_nested():
            await counters.group_same_copy(db, user_id, first_id, second_id)
    except IdentityError:
        pass  # the merge stands; the person can still group the counters by hand
