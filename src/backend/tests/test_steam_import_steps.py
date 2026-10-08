"""Steam batches keep successful imports and enforce user and provider scope."""

import uuid

from src.api.routes import steam_import_steps
from src.database.models.game import Game
from src.database.session import SessionLocal
from tests.test_game_files_flow import game_flow  # noqa: F401


async def create_game(flow, title, source="Steam", app_id="10"):
    response = await flow.client.post(
        "/api/game/create", json={"title": title, "folder_location": title, "source": source}
    )
    assert response.status_code in (200, 201), response.text
    game_id = response.json()["id"]
    async with SessionLocal() as db:
        game = await db.get(Game, uuid.UUID(game_id))
        game.external_id = app_id
        await db.commit()
    return game_id


async def test_enrich_continues_after_failure_and_rolls_back_partial_details(flow, monkeypatch):
    good = await create_game(flow, "Good", app_id="10")
    bad = await create_game(flow, "Bad", app_id="20")
    other_provider = await create_game(flow, "Other", source="GOG", app_id="30")
    calls = []

    async def enrich(game, app_id, *_args):
        calls.append(app_id)
        game.description = "details"
        if app_id == 20:
            raise ValueError("provider unavailable")

    monkeypatch.setattr(steam_import_steps, "_enrich_steam_game_by_appid", enrich)
    response = await flow.client.post(
        "/api/library-sync/steam/enrich",
        json={"game_ids": [bad, good, other_provider, str(uuid.uuid4())]},
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"enriched": 1, "failed": 1}
    assert sorted(calls) == [10, 20]
    async with SessionLocal() as db:
        assert (await db.get(Game, uuid.UUID(good))).description == "details"
        assert (await db.get(Game, uuid.UUID(bad))).description is None
        assert (await db.get(Game, uuid.UUID(other_provider))).description is None


async def test_enrich_rejects_oversized_batches(flow):
    response = await flow.client.post(
        "/api/library-sync/steam/enrich", json={"game_ids": [str(uuid.uuid4()) for _ in range(26)]}
    )
    assert response.status_code == 422


async def test_wishlist_is_disabled_by_default(flow):
    response = await flow.client.post("/api/library-sync/steam/wishlist")
    assert response.status_code == 200, response.text
    assert response.json() == {"enabled": False, "added": 0, "game_ids": []}
