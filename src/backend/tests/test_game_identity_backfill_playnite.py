"""A Playnite library that predates the identity model keeps syncing after the backfill (#102)."""

from uuid import UUID, uuid4

from sqlalchemy import delete, func, select, update

from src.database.models.game import Game
from src.database.models.game_identity import (
    GameSourceConnection,
    GameSourceObservation,
    GameSourceObservationLink,
)
from src.database.models.game_identity_tracking import GameFieldValue
from src.database.session import SessionLocal
from tests.test_playnite_observations import (  # noqa: F401
    NOW,
    batch,
    clients,
    count,
    create_payload,
    entry,
)


async def forget_identity_rows(user_id) -> None:
    """Make an installation look like it was before #94: games only, no source records."""
    async with SessionLocal() as db:
        await db.execute(delete(GameFieldValue).where(GameFieldValue.user_id == user_id))
        await db.execute(
            delete(GameSourceObservation).where(GameSourceObservation.user_id == user_id)
        )
        await db.execute(
            delete(GameSourceConnection).where(GameSourceConnection.user_id == user_id)
        )
        await db.commit()


async def test_full_playnite_sync_after_the_backfill_extends_the_migrated_records(clients):  # noqa: F811
    playnite = await clients()
    user = playnite.user_id
    guids = [str(uuid4()) for _ in range(3)]
    ids = {}
    for guid, title in zip(guids, ("Hades", "Celeste", "HollowKnight"), strict=True):
        created = await playnite.api.post("/api/game/create", json=create_payload(guid, title))
        assert created.status_code == 201, created.text
        ids[guid] = created.json()["id"]
    await forget_identity_rows(user)
    assert await count(GameSourceObservation, user) == 0

    # the user edited one title and set one status by hand before the migration
    locked_id = ids[guids[0]]
    async with SessionLocal() as db:
        await db.execute(
            update(Game)
            .where(Game.id == UUID(locked_id))
            .values(title="Hades (mine)", locked_fields=["title"])
        )
        await db.commit()

    started = await playnite.api.post("/api/game-identity/backfill?wait=true")
    assert started.status_code == 202 and started.json()["status"] == "completed", started.text
    assert await count(GameSourceObservation, user) == 3
    observed = await playnite.api.get("/api/game-identity/backfill")
    assert observed.json()["counts"]["observations_created"] == 3

    # the extension's next full sync: evidence for every game, then the complete inventory
    synced = await playnite.api.post(
        "/api/playnite/observations",
        json=batch(
            *(entry(guid, name="Renamed in Playnite", playtime_seconds=72_000) for guid in guids)
        ),
    )
    assert synced.status_code == 200, synced.text
    assert {r["outcome"] for r in synced.json()["results"]} == {
        "updated"
    }  # extended, never created
    assert [r["game_id"] for r in synced.json()["results"]] == [ids[g] for g in guids]
    inventory = await playnite.api.post(
        "/api/playnite/inventory", json={"contract_version": 1, "playnite_ids": guids}
    )
    assert inventory.status_code == 200, inventory.text
    assert await count(GameSourceObservation, user) == 3  # still one record per Playnite game
    async with SessionLocal() as db:
        assert (
            await db.scalar(
                select(func.count())
                .select_from(GameSourceObservationLink)
                .where(GameSourceObservationLink.user_id == user)
            )
            == 3
        )

    # the original update route still works, protects the user's title and keeps the same game
    updated = await playnite.api.patch(
        f"/api/game/update/{locked_id}", json={"title": "Hades", "playtime_seconds": 80_000}
    )
    assert updated.status_code == 200, updated.text
    assert (
        updated.json()["title"] == "Hades (mine)" and updated.json()["playtime_seconds"] == 80_000
    )
    listed = await playnite.api.get("/api/game/list")
    assert listed.status_code == 200 and len(listed.json()) == 3

    # re-running the backfill after the sync neither duplicates nor rewinds anything
    again = await playnite.api.post("/api/game-identity/backfill?wait=true")
    assert again.json()["status"] == "completed"
    assert again.json()["counts"].get("observations_created", 0) == 0
    async with SessionLocal() as db:
        game = await db.get(Game, locked_id)
        assert game.playtime_seconds == 80_000 and game.title == "Hades (mine)"
        observation = await db.scalar(
            select(GameSourceObservation).where(
                GameSourceObservation.user_id == user,
                GameSourceObservation.external_id == guids[0],
            )
        )
        assert (
            observation.observed_facts["playtime_seconds"] == 80_000
        )  # not rewound by the backfill
