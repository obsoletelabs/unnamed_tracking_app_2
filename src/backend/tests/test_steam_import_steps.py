"""Steam batches keep successful imports and enforce user and provider scope."""

import uuid

import pytest
from sqlalchemy import delete, func, select

from src.api.routes import steam_import_steps
from src.core.auth import get_current_user
from src.core.preferences import save_preferences
from src.database.models.achievement import Achievement
from src.database.models.game import Game, GameStatus
from src.database.models.user import User
from src.database.session import SessionLocal
from src.helpers import save_game_asset
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
        return "complete"

    monkeypatch.setattr(steam_import_steps, "_enrich_steam_game_by_appid", enrich)
    response = await flow.client.post(
        "/api/library-sync/steam/enrich",
        json={"game_ids": [bad, good, other_provider, str(uuid.uuid4())]},
    )
    assert response.status_code == 200, response.text
    assert response.json() == {
        "enriched": 1,
        "failed": 1,
        "incomplete": 0,
        "retry_game_ids": [bad],
    }
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


async def test_wishlist_reimport_keeps_saved_games_and_adds_new_ones(flow, monkeypatch):
    # New wishlist games get a library folder; keep it out of the real data root.
    monkeypatch.setattr(save_game_asset, "DATA_ROOT", flow.tmp)
    await connect_steam(flow)
    enriched = uuid.UUID(await create_game(flow, "Elden Ring", app_id="1245620"))
    async with SessionLocal() as db:
        (await db.get(Game, enriched)).status = GameStatus.WISHLIST
        await save_preferences(db, flow.user_id, {"steam_import_wishlist": True})
    monkeypatch.setattr(
        steam_import_steps.steam_wishlist, "get_wishlist_app_ids", lambda *_: [1245620, 1086940]
    )

    async def recheck(*_args):
        return None

    monkeypatch.setattr(steam_import_steps.steam_connection, "recheck", recheck)
    response = await flow.client.post("/api/library-sync/steam/wishlist")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["wishlisted"] == 2 and body["added"] == 1
    async with SessionLocal() as db:
        assert (await db.get(Game, enriched)).title == "Elden Ring"
        added = await db.get(Game, uuid.UUID(body["game_ids"][0]))
        assert added.external_id == "1086940"
        assert added.status == GameStatus.WISHLIST
    # Running it again adds nothing and renames nothing.
    response = await flow.client.post("/api/library-sync/steam/wishlist")
    assert response.json()["added"] == 0
    async with SessionLocal() as db:
        assert (await db.get(Game, enriched)).title == "Elden Ring"


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


@pytest.mark.parametrize("original", list(GameStatus))
@pytest.mark.parametrize("legacy_hint", [False, True])
async def test_achievement_batch_advances_only_backlog_and_preserves_all_played_choices(
    flow, monkeypatch, original, legacy_hint
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
        json={
            "game_ids": [str(identity)],
            "status_game_ids": [str(identity)] if legacy_hint else [],
        },
    )
    assert response.status_code == 200, response.text
    async with SessionLocal() as db:
        game = await db.get(Game, identity)
        assert game.status == (GameStatus.MASTERED if original == GameStatus.BACKLOG else original)
        assert (game.completion_date is not None) is (original == GameStatus.BACKLOG)


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


def _fake_record(art_urls):
    return {
        "title": "Art",
        "description": "from provider",
        "provider_ids": {"steam": "10"},
        "assets": {},
        "key_art_urls": art_urls,
    }


async def test_enrich_reports_unavailable_artwork_and_retry_is_idempotent(flow, monkeypatch):
    from src.api.routes import library_sync

    game_id = await create_game(flow, "Art", app_id="10")
    artwork_online = False
    saved = []

    async def resolve(*_args, **_kwargs):
        return {"x": 1}, None

    async def download(url, game, kind, **_kwargs):
        if not artwork_online:
            return False
        saved.append((game.id, kind, url))
        return True

    monkeypatch.setattr(library_sync, "resolve_library_record", resolve)
    monkeypatch.setattr(library_sync, "game_result", lambda _record: _fake_record(["u1", "u2"]))
    monkeypatch.setattr(library_sync, "_download_asset", download)
    first = await flow.client.post("/api/library-sync/steam/enrich", json={"game_ids": [game_id]})
    assert first.status_code == 200, first.text
    assert first.json() == {
        "enriched": 0,
        "failed": 0,
        "incomplete": 1,
        "retry_game_ids": [game_id],
    }
    async with SessionLocal() as db:
        # Provider details were kept even though artwork was unavailable.
        assert (await db.get(Game, uuid.UUID(game_id))).description == "from provider"
    artwork_online = True
    for _ in range(2):
        retry = await flow.client.post(
            "/api/library-sync/steam/enrich", json={"game_ids": first.json()["retry_game_ids"]}
        )
        assert retry.json() == {"enriched": 1, "failed": 0, "incomplete": 0, "retry_game_ids": []}
    async with SessionLocal() as db:
        count = await db.scalar(
            select(func.count())
            .select_from(Game)
            .where(Game.title == "Art", Game.source == "Steam")
        )
    assert count == 1


async def test_enrich_without_provider_record_is_retryable_not_enriched(flow, monkeypatch):
    from src.api.routes import library_sync

    game_id = await create_game(flow, "Nothing", app_id="11")

    async def resolve(*_args, **_kwargs):
        return None, None

    monkeypatch.setattr(library_sync, "resolve_library_record", resolve)
    response = await flow.client.post(
        "/api/library-sync/steam/enrich", json={"game_ids": [game_id]}
    )
    assert response.json() == {
        "enriched": 0,
        "failed": 0,
        "incomplete": 1,
        "retry_game_ids": [game_id],
    }


@pytest.mark.parametrize(
    ("status_code", "expected"),
    [
        (403, "rejected the API key"),
        (429, "(429)"),
        (500, "(500)"),
        (503, "(503)"),
    ],
)
def test_owned_games_provider_errors_are_deterministic(status_code, expected, monkeypatch):
    from unittest.mock import Mock

    from src.features.metadata.games import steam

    reply = Mock(status_code=status_code, headers={})
    monkeypatch.setattr(steam.SESSION, "get", Mock(return_value=reply))
    with pytest.raises(
        steam.SteamLibraryError, match=expected.replace("(", r"\(").replace(")", r"\)")
    ):
        steam.get_owned_games("76561190000000000", "key")


def test_owned_games_timeout_is_reported_without_leaking_the_key(monkeypatch):
    from unittest.mock import Mock

    import requests

    from src.features.metadata.games import steam

    timeout = requests.Timeout("https://api.steampowered.com/?key=secret-key")
    monkeypatch.setattr(steam.SESSION, "get", Mock(side_effect=timeout))
    with pytest.raises(steam.SteamLibraryError) as failure:
        steam.get_owned_games("76561190000000000", "secret-key")
    assert "secret-key" not in str(failure.value)
    assert "Could not reach Steam" in str(failure.value)
