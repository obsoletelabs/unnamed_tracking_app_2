"""Steam batches keep successful imports and enforce user and provider scope."""

import uuid

import pytest
from sqlalchemy import delete, select

from src.api.routes import steam_import_steps
from src.core.auth import get_current_user
from src.database.models.achievement import Achievement
from src.database.models.game import Game, GameStatus
from src.database.models.user import User
from src.database.session import SessionLocal
from src.main import app
from tests.test_game_files_flow import game_flow  # noqa: F401


async def create_game(flow, title, source="Steam", app_id="10"):
    response = await flow.client.post(
        "/api/game/create",
        json={"title": title, "folder_location": title.replace(" ", "-"), "source": source},
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


async def connect_steam(flow):
    async with SessionLocal() as db:
        user = await db.get(User, flow.user_id)
        user.steam_id, user.steam_api_key = "76561197960287930", "key"
        await db.commit()
    app.dependency_overrides[get_current_user] = lambda: user


@pytest.mark.parametrize("progress", ["unavailable", "locked", "unlocked", "no-schema", "failed"])
async def test_achievement_batch_preserves_unavailable_progress_and_manual_status(
    flow, monkeypatch, progress
):
    await connect_steam(flow)
    identity = uuid.UUID(await create_game(flow, "Progress"))
    async with SessionLocal() as db:
        game = await db.get(Game, identity)
        game.status = GameStatus.PLAYING
        db.add(
            Achievement(
                game_id=identity, provider="Steam", external_id="WIN", name="Win", unlocked=True
            )
        )
        await db.commit()
    schema = {"WIN": {"displayName": "Win"}}
    monkeypatch.setattr(
        steam_import_steps.steam,
        "get_schema_for_game",
        lambda *_: {} if progress == "no-schema" else schema,
    )

    def player(*_args):
        if progress == "failed":
            raise steam_import_steps.steam.SteamLibraryError("Unavailable")
        return (
            []
            if progress == "unavailable"
            else [{"apiname": "WIN", "achieved": int(progress == "unlocked")}]
        )

    monkeypatch.setattr(steam_import_steps.steam, "get_player_achievements", player)
    response = await flow.client.post(
        "/api/library-sync/steam/achievements",
        json={"game_ids": [str(identity)], "status_game_ids": [str(identity)]},
    )
    assert response.status_code == 200, response.text
    assert response.json()["achievements_unavailable"] == (
        ["Progress"] if progress in {"unavailable", "failed"} else []
    )
    async with SessionLocal() as db:
        game = await db.get(Game, identity)
        saved = (
            await db.execute(select(Achievement).where(Achievement.game_id == identity))
        ).scalar_one()
        assert saved.unlocked is (progress != "locked")
        assert game.status == GameStatus.PLAYING


@pytest.mark.parametrize("original", [GameStatus.PLAYED, GameStatus.PLAYING, GameStatus.BACKLOG])
async def test_achievement_batch_settles_only_unchanged_automatic_status(
    flow, monkeypatch, original
):
    await connect_steam(flow)
    identity = uuid.UUID(await create_game(flow, "Automatic"))
    async with SessionLocal() as db:
        game = await db.get(Game, identity)
        game.status, game.playtime_seconds = original, 60
        await db.commit()
    monkeypatch.setattr(
        steam_import_steps.steam, "get_schema_for_game", lambda *_: {"WIN": {"displayName": "Win"}}
    )
    monkeypatch.setattr(
        steam_import_steps.steam,
        "get_player_achievements",
        lambda *_: [{"apiname": "WIN", "achieved": 1}],
    )
    response = await flow.client.post(
        "/api/library-sync/steam/achievements",
        json={"game_ids": [str(identity)], "status_game_ids": [str(identity)]},
    )
    assert response.status_code == 200, response.text
    async with SessionLocal() as db:
        game = await db.get(Game, identity)
        assert game.status == (GameStatus.MASTERED if original == GameStatus.PLAYED else original)


async def test_achievement_batch_filters_owner_source_and_deleted_games(flow, monkeypatch):
    await connect_steam(flow)
    owned = await create_game(flow, "Owned")
    other_source = await create_game(flow, "Other source", source="GOG")
    trashed = await create_game(flow, "Trashed")
    await flow.client.delete(f"/api/game/delete/{trashed}")
    other_user = User(
        username=f"other_{uuid.uuid4().hex}",
        email=f"{uuid.uuid4()}@example.test",
        password_hash="x",
    )
    async with SessionLocal() as db:
        db.add(other_user)
        await db.flush()
        foreign = Game(
            user_id=other_user.id,
            title="Foreign",
            sort_title="foreign",
            source="Steam",
            external_id="99",
            folder_location="foreign",
        )
        db.add(foreign)
        await db.commit()
        foreign_id, other_id = foreign.id, other_user.id
    asked = []

    def schema(_key, app_id):
        asked.append(app_id)
        return {}

    monkeypatch.setattr(steam_import_steps.steam, "get_schema_for_game", schema)
    try:
        response = await flow.client.post(
            "/api/library-sync/steam/achievements",
            json={"game_ids": [owned, other_source, trashed, str(foreign_id)]},
        )
        assert response.status_code == 200, response.text
        assert asked == [10]
        assert response.json() == {"achievements_synced": 0, "achievements_unavailable": []}
    finally:
        async with SessionLocal() as db:
            await db.execute(delete(User).where(User.id == other_id))
            await db.commit()


@pytest.mark.parametrize("change", ["external_id", "source", "deleted_at"])
async def test_achievement_batch_rechecks_identity_after_provider_requests(
    flow, monkeypatch, change
):
    await connect_steam(flow)
    identity = uuid.UUID(await create_game(flow, "Changed"))

    async def fetch(*_args):
        async with SessionLocal() as db:
            game = await db.get(Game, identity)
            setattr(game, change, {"external_id": "999", "source": "GOG", "deleted_at": 1}[change])
            await db.commit()
        return {"WIN": {"displayName": "Win"}}, [{"apiname": "WIN", "achieved": 1}], None

    monkeypatch.setattr(steam_import_steps, "fetch_steam_achievements", fetch)
    response = await flow.client.post(
        "/api/library-sync/steam/achievements", json={"game_ids": [str(identity)]}
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"achievements_synced": 0, "achievements_unavailable": []}


async def test_achievement_batch_rejects_oversized_requests(flow):
    response = await flow.client.post(
        "/api/library-sync/steam/achievements",
        json={"game_ids": [str(uuid.uuid4()) for _ in range(26)]},
    )
    assert response.status_code == 422


async def test_achievement_batch_preserves_status_changed_during_provider_request(
    flow, monkeypatch
):
    await connect_steam(flow)
    identity = uuid.UUID(await create_game(flow, "ChangedStatus"))
    async with SessionLocal() as db:
        game = await db.get(Game, identity)
        game.status, game.playtime_seconds = GameStatus.PLAYED, 60
        await db.commit()

    async def fetch(*_args):
        async with SessionLocal() as db:
            game = await db.get(Game, identity)
            game.status = GameStatus.PLAYING
            await db.commit()
        return {"WIN": {"displayName": "Win"}}, [{"apiname": "WIN", "achieved": 1}], None

    monkeypatch.setattr(steam_import_steps, "fetch_steam_achievements", fetch)
    response = await flow.client.post(
        "/api/library-sync/steam/achievements",
        json={"game_ids": [str(identity)], "status_game_ids": [str(identity)]},
    )
    assert response.status_code == 200, response.text
    assert response.json()["achievements_synced"] == 1
    async with SessionLocal() as db:
        assert (await db.get(Game, identity)).status == GameStatus.PLAYING
