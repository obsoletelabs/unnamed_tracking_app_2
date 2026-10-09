"""Bulk Steam sync skips unnecessary requests and preserves unavailable progress."""

from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import delete, select

from src.api.routes import library_sync
from src.database.models.achievement import Achievement
from src.database.models.game import Game, GameStatus
from src.database.models.user import User
from src.database.session import SessionLocal


@pytest.fixture
async def steam_owner():
    async with SessionLocal() as db:
        user = User(
            username=f"steam_{uuid4().hex}",
            email=f"{uuid4()}@example.test",
            password_hash="x",
            steam_id="76561190000000000",
            steam_api_key="test-key",
        )
        db.add(user)
        await db.commit()
        identity = user.id
    yield identity
    async with SessionLocal() as db:
        await db.execute(delete(User).where(User.id == identity))
        await db.commit()


async def test_private_library_does_not_mark_owned_games_stale(steam_owner, monkeypatch):
    async with SessionLocal() as db:
        user = await db.get(User, steam_owner)
        game = Game(
            user_id=user.id,
            title="Owned Steam game",
            sort_title="owned steam game",
            folder_location="owned-steam-game",
            source="Steam",
            external_id="123",
            status=GameStatus.PLAYING,
        )
        db.add(game)
        await db.commit()
        game_id = game.id

    reply = Mock(status_code=200)
    reply.json.return_value = {"response": {}}
    monkeypatch.setattr(library_sync.steam.SESSION, "get", Mock(return_value=reply))
    monkeypatch.setattr(library_sync.steam, "resolve_steam_id", lambda sid, _key: sid)
    async with SessionLocal() as db:
        user = await db.get(User, steam_owner)
        with pytest.raises(HTTPException) as failure:
            await library_sync.sync_steam_library(db=db, current_user=user)
        assert failure.value.status_code == 502
        assert "Game details to Public" in failure.value.detail

    async with SessionLocal() as db:
        game = await db.get(Game, game_id)
        user = await db.get(User, steam_owner)
        assert game.stale_since is None
        assert game.status == GameStatus.PLAYING
        assert user.steam_library_synced_at is None


@pytest.mark.parametrize("response_kind", ["no-schema", "unavailable", "failed", "locked"])
async def test_bulk_sync_achievement_requests_and_saved_progress(
    steam_owner, monkeypatch, response_kind
):
    async with SessionLocal() as db:
        user = await db.get(User, steam_owner)
        game = Game(
            user_id=user.id,
            title="Existing Steam game",
            sort_title="existing steam game",
            folder_location="existing-steam-game",
            source="Steam",
            external_id="123",
            status=GameStatus.PLAYING,
        )
        db.add(game)
        await db.flush()
        achievement = Achievement(
            game_id=game.id,
            provider="Steam",
            external_id="ACH_WIN",
            name="Win",
            unlocked=True,
            unlocked_at=123456,
        )
        db.add(achievement)
        await db.commit()
        game_id, achievement_id = game.id, achievement.id

    monkeypatch.setattr(library_sync.steam, "resolve_steam_id", lambda sid, _key: sid)
    monkeypatch.setattr(
        library_sync.steam,
        "get_owned_games",
        lambda *_: [{"appid": 123, "name": "Existing Steam game", "playtime_forever": 5}],
    )
    schema = {
        "ACH_WIN": {"displayName": "Win"},
        "ACH_NEW": {"displayName": "Another achievement"},
    }
    monkeypatch.setattr(
        library_sync.steam,
        "get_schema_for_game",
        lambda *_: {} if response_kind == "no-schema" else schema,
    )
    player_calls = []

    def player(*_args):
        player_calls.append(123)
        if response_kind == "failed":
            raise library_sync.steam.SteamLibraryError("Provider unavailable")
        if response_kind == "locked":
            return [{"apiname": "ACH_WIN", "achieved": 0}, {"apiname": "ACH_NEW", "achieved": 0}]
        return []

    monkeypatch.setattr(library_sync.steam, "get_player_achievements", player)

    def forbidden(*_args):
        raise AssertionError("No community request is needed for these schemas")

    monkeypatch.setattr(library_sync.steam, "get_community_descriptions", forbidden)
    async with SessionLocal() as db:
        user = await db.get(User, steam_owner)
        result = await library_sync.sync_steam_library(db, user)
    assert player_calls == ([] if response_kind == "no-schema" else [123])
    assert result["games_updated"] == 1
    assert result["games_added"] == 0
    async with SessionLocal() as db:
        game = await db.get(Game, game_id)
        assert game.playtime_seconds == 300
        assert game.status == GameStatus.PLAYING
        rows = (await db.scalars(select(Achievement).where(Achievement.game_id == game_id))).all()
        if response_kind == "locked":
            assert result["achievements_synced"] == 2
            assert len(rows) == 2
            assert all(not row.unlocked and row.unlocked_at is None for row in rows)
        else:
            assert result["achievements_synced"] == 0
            assert len(rows) == 1
            assert rows[0].id == achievement_id
            assert rows[0].unlocked and rows[0].unlocked_at == 123456
