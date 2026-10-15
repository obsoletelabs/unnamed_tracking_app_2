"""Bulk Steam sync skips unnecessary requests and preserves unavailable progress."""

from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import delete, select

from src.api.routes import library_sync, steam_import_steps
from src.database.models.achievement import Achievement
from src.database.models.game import Game, GameStatus
from src.database.models.user import User
from src.database.session import SessionLocal
from src.helpers import save_game_asset


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


@pytest.mark.parametrize("returned_apps", [[], [{"appid": 1230410}]])
@pytest.mark.parametrize("previous_warning", [None, 123456])
async def test_steam_omissions_repair_warnings_without_changing_saved_games(
    steam_owner, monkeypatch, returned_apps, previous_warning
):
    async with SessionLocal() as db:
        games = [
            Game(
                user_id=steam_owner,
                title=title,
                sort_title=title.lower(),
                folder_location=f"omission-{app_id}",
                source=source,
                external_id=str(app_id),
                status=GameStatus.PLAYING,
                playtime_seconds=900,
                stale_since=previous_warning,
                deleted_at=deleted_at,
            )
            for title, app_id, source, deleted_at in [
                ("Chaos", 1230410, "Steam", None),
                ("Example demo", 123, "Steam", None),
                ("Other provider", 456, "Epic Games", None),
                ("Trashed demo", 789, "Steam", 123456),
            ]
        ]
        db.add_all(games)
        await db.commit()
        identities = [game.id for game in games]
    monkeypatch.setattr(library_sync.steam, "get_owned_games", lambda *_: returned_apps)
    async with SessionLocal() as db:
        user = await db.get(User, steam_owner)
        result = await library_sync.sync_steam_library(db, user, achievements="later")
        assert result["games_flagged_stale"] == 0
        assert result["games_added"] == result["games_updated"] == 0
    async with SessionLocal() as db:
        for position, identity in enumerate(identities):
            game = await db.get(Game, identity)
            assert game.stale_since == (None if position < 2 else previous_warning)
            assert game.playtime_seconds == 900
            assert game.status == GameStatus.PLAYING
        user = await db.get(User, steam_owner)
        assert user.steam_library_synced_at is not None


async def test_steam_warning_repair_is_scoped_to_the_current_user(steam_owner, monkeypatch):
    async with SessionLocal() as db:
        other_user = User(
            username=f"other_steam_{uuid4().hex}",
            email=f"{uuid4()}@example.test",
            password_hash="x",
        )
        db.add(other_user)
        await db.flush()
        other_id = other_user.id
        game = Game(
            user_id=other_id,
            title="Other user's demo",
            sort_title="other user's demo",
            folder_location="other-steam-demo",
            source="Steam",
            external_id="123",
            stale_since=123456,
        )
        db.add(game)
        await db.commit()
        game_id = game.id
    try:
        monkeypatch.setattr(library_sync.steam, "get_owned_games", lambda *_: [])
        async with SessionLocal() as db:
            user = await db.get(User, steam_owner)
            await library_sync.sync_steam_library(db, user, achievements="later")
        async with SessionLocal() as db:
            game = await db.get(Game, game_id)
            assert game.stale_since == 123456
    finally:
        async with SessionLocal() as db:
            await db.execute(delete(User).where(User.id == other_id))
            await db.commit()


async def test_steam_missing_playtime_does_not_reset_saved_progress(steam_owner, monkeypatch):
    async with SessionLocal() as db:
        game = Game(
            user_id=steam_owner,
            title="Chaos",
            sort_title="chaos",
            folder_location="chaos-saved-progress",
            source="Steam",
            external_id="1230410",
            status=GameStatus.PLAYING,
            playtime_seconds=900,
            last_played_at=123456,
        )
        db.add(game)
        await db.commit()
        identity = game.id
    monkeypatch.setattr(
        library_sync.steam, "get_owned_games", lambda *_: [{"appid": 1230410, "name": "Chaos"}]
    )
    async with SessionLocal() as db:
        user = await db.get(User, steam_owner)
        await library_sync.sync_steam_library(db, user, achievements="later")
    async with SessionLocal() as db:
        game = await db.get(Game, identity)
        assert game.playtime_seconds == 900
        assert game.last_played_at == 123456
        assert game.status == GameStatus.PLAYING


async def test_deferred_sync_saves_games_without_achievement_requests(
    steam_owner, monkeypatch, tmp_path
):
    monkeypatch.setattr(save_game_asset, "DATA_ROOT", tmp_path)
    monkeypatch.setattr(library_sync.steam, "resolve_steam_id", lambda sid, _key: sid)
    monkeypatch.setattr(
        library_sync.steam,
        "get_owned_games",
        lambda *_: [{"appid": 123, "name": "Deferred game", "playtime_forever": 5}],
    )
    schema = Mock(side_effect=AssertionError("Owned games must be saved before achievements"))
    monkeypatch.setattr(library_sync.steam, "get_schema_for_game", schema)
    async with SessionLocal() as db:
        user = await db.get(User, steam_owner)
        result = await library_sync.sync_steam_library(
            achievements="later", db=db, current_user=user
        )
        assert result["games_added"] == 1
        assert result["achievements_synced"] == 0
        assert result["achievement_game_ids"] == result["status_game_ids"]
        identity = result["achievement_game_ids"][0]
    async with SessionLocal() as db:
        game = await db.scalar(select(Game).where(Game.user_id == steam_owner))
        assert str(game.id) == identity
        assert game.playtime_seconds == 300
        # The achievement batches decide between Played and Mastered.
        assert game.status == GameStatus.BACKLOG
    schema.assert_not_called()


@pytest.mark.parametrize(
    ("progress", "expected"),
    [
        ("complete", GameStatus.MASTERED),
        ("partial", GameStatus.PLAYED),
        ("unavailable", GameStatus.PLAYED),
        ("no-schema", GameStatus.PLAYED),
    ],
)
async def test_deferred_achievement_batches_finish_status_progression(
    steam_owner, monkeypatch, tmp_path, progress, expected
):
    """Settings > Sync now defers achievements; a fully unlocked game still becomes Mastered."""
    monkeypatch.setattr(save_game_asset, "DATA_ROOT", tmp_path)
    monkeypatch.setattr(library_sync.steam, "resolve_steam_id", lambda sid, _key: sid)
    monkeypatch.setattr(
        library_sync.steam,
        "get_owned_games",
        lambda *_: [{"appid": 123, "name": "Deferred game", "playtime_forever": 600}],
    )
    monkeypatch.setattr(
        steam_import_steps.steam,
        "get_schema_for_game",
        lambda *_: (
            {}
            if progress == "no-schema"
            else {"A": {"displayName": "A"}, "B": {"displayName": "B"}}
        ),
    )

    def player(*_args):
        if progress == "unavailable":
            raise steam_import_steps.steam.SteamLibraryError("Private profile")
        return [
            {"apiname": "A", "achieved": 1},
            {"apiname": "B", "achieved": int(progress == "complete")},
        ]

    monkeypatch.setattr(steam_import_steps.steam, "get_player_achievements", player)
    async with SessionLocal() as db:
        user = await db.get(User, steam_owner)
        result = await library_sync.sync_steam_library(
            achievements="later", db=db, current_user=user
        )
    async with SessionLocal() as db:
        user = await db.get(User, steam_owner)
        await steam_import_steps.import_achievements(
            steam_import_steps.AchievementsRequest(
                game_ids=result["achievement_game_ids"],
                status_game_ids=result["status_game_ids"],
            ),
            db=db,
            current_user=user,
        )
    async with SessionLocal() as db:
        game = await db.scalar(select(Game).where(Game.user_id == steam_owner))
        assert game.status == expected
        assert (game.completion_date is not None) is (expected == GameStatus.MASTERED)


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
