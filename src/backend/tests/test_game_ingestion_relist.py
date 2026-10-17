"""A record that reappears in a source listing is available again."""

from sqlalchemy import select

from src.database.models.game_identity import GameSourceObservation
from src.database.session import SessionLocal
from src.features.game_ingestion import legacy
from src.features.game_ingestion.service import complete_inventory
from tests.test_game_ingestion import make_game, users  # noqa: F401


async def test_relisted_record_becomes_available(users):  # noqa: F811
    user = await users()
    game = await make_game(user)
    async with SessionLocal() as db:
        obs = legacy.build_observation("77", title="Hades", owned=True)
        await legacy.record_observations(db, user, legacy.RETROACHIEVEMENTS, [(game, obs)])
        await complete_inventory(db, user, legacy.RETROACHIEVEMENTS, set())
        await db.commit()
    async with SessionLocal() as db:
        row = await db.scalar(
            select(GameSourceObservation).where(GameSourceObservation.user_id == user)
        )
        assert row.availability == "unavailable"
    async with SessionLocal() as db:
        obs = legacy.build_observation("77", title="Hades", owned=True)
        await legacy.record_observations(db, user, legacy.RETROACHIEVEMENTS, [(game, obs)])
        await db.commit()
    async with SessionLocal() as db:
        row = await db.scalar(
            select(GameSourceObservation).where(GameSourceObservation.user_id == user)
        )
        assert row.availability == "available"
