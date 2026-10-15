"""Fresh provider writes advance eligible games without rewriting personal choices."""

from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from sqlalchemy import select

from src.api.routes import library_sync
from src.core.crypto import encrypt_secret
from src.database.models.achievement import Achievement
from src.database.models.game import Game, GameStatus
from src.database.models.user import User
from src.database.session import SessionLocal
from src.helpers import save_game_asset
from src.plugin_api.game_import import dispatch_game_import
from tests.test_game_files_flow import game_flow  # noqa: F401

SOURCES = ["Steam", "RetroAchievements", "PlayStation"]


@pytest.fixture(autouse=True)
def isolated_import_storage(tmp_path, monkeypatch):
    monkeypatch.setattr(save_game_asset, "DATA_ROOT", tmp_path)


async def provider_game(flow, source, original):
    async with SessionLocal() as db:
        game = await db.get(Game, flow.game_id)
        game.source, game.external_id, game.platform = source, "10", "PS5"
        game.status = original
        game.resume_note = "Keep my note"
        game.rating_overall = 8
        user = await db.get(User, flow.user_id)
        user.steam_id, user.steam_api_key = "76561197960287930", "key"
        user.retroachievements_username, user.retroachievements_api_key = "player", "key"
        user.psn_npsso_token = encrypt_secret("token")
        await db.commit()


def owned_snapshot(monkeypatch, source, *, unlocked=1, total=1, playtime=None):
    """Patch remote clients while retaining the real import/row-lock/write path."""
    if source == "Steam":
        entry = {"appid": 10, "name": "Flow"}
        if playtime is not None:
            entry["playtime_forever"] = playtime
        monkeypatch.setattr(library_sync.steam, "get_owned_games", lambda *_: [entry])
        monkeypatch.setattr(library_sync.steam, "resolve_steam_id", lambda identity, *_: identity)
        monkeypatch.setattr(
            library_sync,
            "fetch_steam_achievements",
            AsyncMock(
                return_value=(
                    {str(index): {"displayName": f"Win {index}"} for index in range(total)},
                    [
                        {"apiname": str(index), "achieved": int(index < unlocked)}
                        for index in range(total)
                    ],
                    None,
                )
            ),
        )
    elif source == "RetroAchievements":
        monkeypatch.setattr(
            library_sync.RetroAchievementsClient,
            "get_user_games",
            lambda *_: [{"GameID": 10, "Title": "Flow"}],
        )
        monkeypatch.setattr(
            library_sync.RetroAchievementsClient,
            "get_game_progress",
            lambda *_: {
                "Achievements": {
                    str(index): {
                        "Title": f"Win {index}",
                        "DateEarned": "2026-10-15" if index < unlocked else None,
                    }
                    for index in range(total)
                }
            },
        )
    else:
        monkeypatch.setattr(
            library_sync.PSNClient,
            "get_owned_titles",
            lambda *_: [
                {"npCommunicationId": "10", "trophyTitleName": "Flow", "trophyTitlePlatform": "PS5"}
            ],
        )
        monkeypatch.setattr(
            library_sync.PSNClient,
            "get_trophies_for_title",
            lambda *_: [
                {"trophyId": str(index), "trophyName": f"Win {index}", "earned": index < unlocked}
                for index in range(total)
            ],
        )


@pytest.mark.parametrize("source", SOURCES)
@pytest.mark.parametrize("original", list(GameStatus))
async def test_owned_sync_advances_eligible_games_and_preserves_every_other_status(
    flow, monkeypatch, source, original
):
    await provider_game(flow, source, original)
    owned_snapshot(monkeypatch, source)
    handler = {
        "Steam": library_sync.sync_steam_library,
        "RetroAchievements": library_sync.sync_retroachievements_library,
        "PlayStation": library_sync.sync_psn_library,
    }[source]
    async with SessionLocal() as db:
        await handler(db, await db.get(User, flow.user_id))
    async with SessionLocal() as db:
        game = await db.get(Game, flow.game_id)
        eligible = original in {GameStatus.BACKLOG, GameStatus.WISHLIST}
        assert game.status == (GameStatus.MASTERED if eligible else original)
        assert (game.completion_date is not None) is eligible
        assert game.resume_note == "Keep my note" and game.rating_overall == 8
        achievement = await db.scalar(select(Achievement).where(Achievement.game_id == game.id))
        assert achievement.unlocked


@pytest.mark.parametrize("source", SOURCES)
@pytest.mark.parametrize("unlocked", [0, 1, 2])
async def test_owned_sync_distinguishes_no_partial_and_full_achievement_progress(
    flow, monkeypatch, source, unlocked
):
    await provider_game(flow, source, GameStatus.WISHLIST)
    owned_snapshot(monkeypatch, source, unlocked=unlocked, total=2)
    handler = {
        "Steam": library_sync.sync_steam_library,
        "RetroAchievements": library_sync.sync_retroachievements_library,
        "PlayStation": library_sync.sync_psn_library,
    }[source]
    async with SessionLocal() as db:
        await handler(db, await db.get(User, flow.user_id))
    async with SessionLocal() as db:
        game = await db.get(Game, flow.game_id)
        assert game.status == [GameStatus.BACKLOG, GameStatus.PLAYED, GameStatus.MASTERED][unlocked]
        assert (game.completion_date is not None) is (unlocked == 2)


async def test_automatic_mastery_preserves_an_existing_completion_date(flow, monkeypatch):
    await provider_game(flow, "Steam", GameStatus.BACKLOG)
    async with SessionLocal() as db:
        (await db.get(Game, flow.game_id)).completion_date = 123456
        await db.commit()
    owned_snapshot(monkeypatch, "Steam")
    for _ in range(2):
        async with SessionLocal() as db:
            await library_sync.sync_steam_library(db, await db.get(User, flow.user_id))
        async with SessionLocal() as db:
            game = await db.get(Game, flow.game_id)
            assert game.status == GameStatus.MASTERED and game.completion_date == 123456


@pytest.mark.parametrize("original", [GameStatus.BACKLOG, GameStatus.WISHLIST])
@pytest.mark.parametrize("playtime", [None, 0, 5])
async def test_owned_steam_uses_reported_playtime_not_a_locally_saved_value(
    flow, monkeypatch, original, playtime
):
    await provider_game(flow, "Steam", original)
    async with SessionLocal() as db:
        (await db.get(Game, flow.game_id)).playtime_seconds = 900
        await db.commit()
    owned_snapshot(monkeypatch, "Steam", unlocked=0, playtime=playtime)
    async with SessionLocal() as db:
        await library_sync.sync_steam_library(db, await db.get(User, flow.user_id))
    async with SessionLocal() as db:
        game = await db.get(Game, flow.game_id)
        assert game.status == (GameStatus.PLAYED if playtime else GameStatus.BACKLOG)
        assert game.completion_date is None
        assert game.playtime_seconds == (900 if playtime is None else playtime * 60)


@pytest.mark.parametrize("source", SOURCES)
@pytest.mark.parametrize("original", [GameStatus.BACKLOG, GameStatus.WISHLIST, GameStatus.PLAYED])
async def test_achievement_refresh_does_not_infer_wishlist_ownership_or_change_played(
    flow, monkeypatch, source, original
):
    await provider_game(flow, source, original)
    rows = [{"external_id": "WIN", "name": "Win", "unlocked": True}]
    target = {
        "Steam": "_fetch_steam_rows",
        "RetroAchievements": "_fetch_retro_rows",
        "PlayStation": "_fetch_psn_rows",
    }[source]
    monkeypatch.setattr(library_sync, target, AsyncMock(return_value=rows))
    response = await flow.client.post(f"/api/library-sync/games/{flow.game_id}/achievements")
    assert response.status_code == 200, response.text
    assert response.json()["unlocked"] == 1
    async with SessionLocal() as db:
        game = await db.get(Game, flow.game_id)
        assert game.status == (GameStatus.MASTERED if original == GameStatus.BACKLOG else original)


@pytest.mark.parametrize("source", SOURCES)
@pytest.mark.parametrize("change", ["status", "source", "external_id", "deleted_at"])
async def test_refresh_rechecks_identity_and_manual_status_after_provider_work(
    flow, monkeypatch, source, change
):
    await provider_game(flow, source, GameStatus.BACKLOG)

    async def fetch(*_args):
        async with SessionLocal() as db:
            game = await db.get(Game, flow.game_id)
            value = {
                "status": GameStatus.PLAYED,
                "source": "GOG",
                "external_id": "99",
                "deleted_at": 1,
            }[change]
            setattr(game, change, value)
            await db.commit()
        return [{"external_id": "WIN", "name": "Win", "unlocked": True}]

    target = {
        "Steam": "_fetch_steam_rows",
        "RetroAchievements": "_fetch_retro_rows",
        "PlayStation": "_fetch_psn_rows",
    }[source]
    monkeypatch.setattr(library_sync, target, fetch)
    response = await flow.client.post(f"/api/library-sync/games/{flow.game_id}/achievements")
    assert response.status_code == (200 if change == "status" else 409), response.text
    async with SessionLocal() as db:
        game = await db.get(Game, flow.game_id)
        assert game.status == (GameStatus.PLAYED if change == "status" else GameStatus.BACKLOG)
        assert game.completion_date is None
        rows = list(await db.scalars(select(Achievement).where(Achievement.game_id == game.id)))
        assert len(rows) == (1 if change == "status" else 0)


@pytest.mark.parametrize("original", list(GameStatus))
async def test_public_plugin_import_applies_same_policy_without_a_contract_change(flow, original):
    payload = {
        "source_label": "Example store",
        "source_scope": "account:owned",
        "items": [{"external_id": "10", "title": "Progress"}],
    }
    async with SessionLocal() as db:
        first = await dispatch_game_import(
            db, plugin_id="test.import", user_id=flow.user_id, payload=payload
        )
        identity = UUID(first["games"][0]["id"])
        game = await db.get(Game, identity)
        game.status, game.playtime_seconds = original, 900
        game.locked_fields = ["playtime_seconds"]
        await db.commit()
        # An inventory without playtime can establish ownership, not prior play.
        second = await dispatch_game_import(
            db, plugin_id="test.import", user_id=flow.user_id, payload=payload
        )
        assert second["games"][0]["status"] == (
            "BACKLOG" if original == GameStatus.WISHLIST else original.value
        )
        payload["items"][0]["playtime_seconds"] = 60
        third = await dispatch_game_import(
            db, plugin_id="test.import", user_id=flow.user_id, payload=payload
        )
        assert third["games"][0]["status"] == (
            "PLAYED" if original in {GameStatus.BACKLOG, GameStatus.WISHLIST} else original.value
        )
        assert third["games"][0]["playtime_seconds"] == 900


async def test_reading_achievements_never_rewrites_a_historical_status(flow):
    await provider_game(flow, "Steam", GameStatus.BACKLOG)
    async with SessionLocal() as db:
        db.add(
            Achievement(
                game_id=flow.game_id, provider="Steam", external_id="WIN", name="Win", unlocked=True
            )
        )
        await db.commit()
    response = await flow.client.get(f"/api/game/{flow.game_id}/achievements")
    assert response.status_code == 200 and response.json()[0]["unlocked"] is True
    async with SessionLocal() as db:
        game = await db.get(Game, flow.game_id)
        assert game.status == GameStatus.BACKLOG and game.completion_date is None
