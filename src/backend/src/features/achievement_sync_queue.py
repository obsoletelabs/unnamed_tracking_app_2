"""Queue games whose achievement catalogue or progress needs a background refresh."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models.achievement_sync_queue import AchievementSyncQueue
from src.database.models.game import Game


async def enqueue_game(db: AsyncSession, user_id: UUID, game_id: UUID) -> None:
    """Persist one pending refresh, resetting an earlier failed attempt if retried."""
    row = await db.get(AchievementSyncQueue, game_id)
    if row is None:
        db.add(AchievementSyncQueue(game_id=game_id, user_id=user_id))
    else:
        row.user_id = user_id
        row.attempts = 0
        row.last_error = None
    await db.flush()


async def enqueue_user_games(db: AsyncSession, user_id: UUID) -> int:
    """Queue every active game for a full manual or scheduled sync."""
    game_ids = list(
        await db.scalars(select(Game.id).where(Game.user_id == user_id, Game.deleted_at.is_(None)))
    )
    for game_id in game_ids:
        await enqueue_game(db, user_id, game_id)
    return len(game_ids)
