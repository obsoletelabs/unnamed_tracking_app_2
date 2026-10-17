"""Overview statistics and the weekly digest count active games only.

Trashing a game through the real route removes every one of its contributions
(counts, sums, rating, breakdowns, storage, achievements, metadata changes);
restoring it brings back exactly the same numbers. Another account's games,
active or trashed, never contribute, and media statistics stay as they were."""

import time
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import delete, update

from src.api.routes import stats
from src.core.auth import get_current_user
from src.database.models.achievement import Achievement
from src.database.models.game import Game, GameStatus
from src.database.models.game_field_change import GameFieldChange
from src.database.models.movies import Movie, MovieStatus
from src.database.models.user import User
from src.database.session import SessionLocal
from src.main import app
from tests.test_game_files_flow import game_flow  # noqa: F401

_DAY = 86_400
_SIZES = {"Flow": 100, "kept-folder": 10}
_TOTALS = (
    "total_games",
    "favorite_count",
    "total_playtime_seconds",
    "storage_used_bytes",
    "total_spent",
    "average_rating",
)
_UNORDERED = (
    "status_breakdown",
    "source_breakdown",
    "rating_histogram",
    "top_tags",
    "format_breakdown",
)


def _achievement(game_id, external_id, unlocked_at):
    return Achievement(
        game_id=game_id,
        provider="Steam",
        external_id=external_id,
        name=external_id,
        unlocked=unlocked_at is not None,
        unlocked_at=unlocked_at,
    )


def _change(game_id, changed_at):
    return GameFieldChange(
        game_id=game_id,
        field_name="developer",
        old_value=None,
        new_value="Studio",
        changed_at=changed_at,
    )


@pytest.fixture
async def library(flow, monkeypatch):
    """Two active games for the caller (the flow game is the one trashed),
    plus a stranger with an active and a trashed game that must never count."""
    measured: list[str] = []

    def fake_folder_size(folder):
        measured.append(folder)
        return _SIZES.get(folder, 1_000_000)

    monkeypatch.setattr(stats, "_folder_size_bytes", fake_folder_size)
    now = int(time.time())
    async with SessionLocal() as db:
        await db.execute(
            update(Game)
            .where(Game.id == flow.game_id)
            .values(
                status=GameStatus.BEATEN,
                favorite=True,
                playtime_seconds=7200,
                purchase_price=Decimal("0.20"),
                rating_overall=Decimal("8.50"),
                tags=["RPG", "Trashed only"],
                source="GOG",
                release_date=date(2021, 3, 1),
                physical_condition="Boxed",
                created_at=now - _DAY,
                last_played_at=now - 2 * 3600,
            )
        )
        kept = Game(
            user_id=flow.user_id,
            title="Kept",
            sort_title="kept",
            folder_location="kept-folder",
            status=GameStatus.PLAYING,
            favorite=True,
            playtime_seconds=3600,
            purchase_price=Decimal("10.10"),
            rating_overall=Decimal("7.25"),
            tags=["RPG", "Indie"],
            source="Steam",
            release_date=date(2020, 5, 1),
            created_at=now - 30 * _DAY,
            last_played_at=now - _DAY,
        )
        stranger = User(
            username=uuid4().hex, email=f"{uuid4().hex}@example.test", password_hash="x"
        )
        db.add_all([kept, stranger])
        await db.flush()
        foreign = [
            Game(
                user_id=stranger.id,
                title=title,
                sort_title=title.lower(),
                folder_location=title.lower(),
                status=GameStatus.MASTERED,
                favorite=True,
                playtime_seconds=999_999,
                purchase_price=Decimal("99.99"),
                rating_overall=Decimal("1.00"),
                tags=["Foreign"],
                source="Epic Games",
                created_at=now - 3600,
                last_played_at=now - 3600,
                deleted_at=deleted_at,
            )
            for title, deleted_at in (("Foreign", None), ("ForeignTrash", now - 60))
        ]
        db.add_all(foreign)
        await db.flush()
        db.add_all(
            [
                _achievement(flow.game_id, "trashed-1", now - 3600),
                _achievement(flow.game_id, "trashed-2", now - 2 * _DAY),
                _achievement(flow.game_id, "trashed-locked", None),
                _change(flow.game_id, now - 3600),
                _achievement(kept.id, "kept-1", now - 3600),
                _achievement(kept.id, "kept-old", now - 30 * _DAY),
                _change(kept.id, now - 3600),
                _change(kept.id, now - 30 * _DAY),
                *(_achievement(game.id, "foreign", now - 60) for game in foreign),
                *(_change(game.id, now - 60) for game in foreign),
            ]
        )
        await db.commit()
        stranger_id = stranger.id
    flow.measured, flow.kept_id, flow.stranger_id = measured, kept.id, stranger_id
    yield flow
    async with SessionLocal() as db:
        await db.execute(delete(User).where(User.id == stranger_id))
        await db.commit()


async def _overview(flow) -> dict:
    flow.measured.clear()
    response = await flow.client.get("/api/stats/overview")
    assert response.status_code == 200, response.text
    data = response.json()
    for key in _UNORDERED:
        data[key] = sorted(data[key], key=lambda row: row["label"])
    return data


async def _digest(flow) -> dict:
    response = await flow.client.get("/api/stats/weekly-digest")
    assert response.status_code == 200, response.text
    data = response.json()
    assert abs(data.pop("period_start") - (int(time.time()) - 7 * _DAY)) <= 5
    return data


async def _trash(flow):
    assert (await flow.client.delete(f"/api/game/delete/{flow.game_id}")).status_code == 204


async def _restore(flow):
    assert (await flow.client.post(f"/api/game/{flow.game_id}/restore")).status_code == 200


async def test_overview_drops_trashed_game_and_restore_brings_it_back(library):
    before = await _overview(library)
    assert sorted(library.measured) == ["Flow", "kept-folder"]
    assert {key: before[key] for key in _TOTALS} == {
        "total_games": 2,
        "favorite_count": 2,
        "total_playtime_seconds": 10_800,
        "storage_used_bytes": 110,
        # summed as NUMERIC, so 10.10 + 0.20 is exactly 10.3, not 10.299999...
        "total_spent": 10.3,
        "average_rating": 7.875,
    }
    assert before["status_breakdown"] == [
        {"label": "BEATEN", "count": 1},
        {"label": "PLAYING", "count": 1},
    ]
    assert before["source_breakdown"] == [
        {"label": "GOG", "count": 1},
        {"label": "Steam", "count": 1},
    ]
    assert before["most_played"] == [
        {"id": str(library.game_id), "title": "Flow", "playtime_seconds": 7200},
        {"id": str(library.kept_id), "title": "Kept", "playtime_seconds": 3600},
    ]
    assert sum(row["count"] for row in before["recently_added"]) == 2
    assert before["rating_histogram"] == [
        {"label": "7-8", "count": 1},
        {"label": "8-9", "count": 1},
    ]
    assert before["top_tags"] == [
        {"label": "Indie", "count": 1},
        {"label": "RPG", "count": 2},
        {"label": "Trashed only", "count": 1},
    ]
    assert before["release_year_breakdown"] == [
        {"label": "2020", "count": 1},
        {"label": "2021", "count": 1},
    ]
    assert before["format_breakdown"] == [
        {"label": "Digital", "count": 1},
        {"label": "Physical", "count": 1},
    ]

    await _trash(library)
    trashed = await _overview(library)
    assert library.measured == ["kept-folder"]
    assert trashed == {
        "total_games": 1,
        "favorite_count": 1,
        "total_playtime_seconds": 3600,
        "storage_used_bytes": 10,
        "total_spent": 10.1,
        "average_rating": 7.25,
        "status_breakdown": [{"label": "PLAYING", "count": 1}],
        "source_breakdown": [{"label": "Steam", "count": 1}],
        "most_played": [before["most_played"][1]],
        "recently_added": trashed["recently_added"],
        "rating_histogram": [{"label": "7-8", "count": 1}],
        "top_tags": [{"label": "Indie", "count": 1}, {"label": "RPG", "count": 1}],
        "release_year_breakdown": [{"label": "2020", "count": 1}],
        "format_breakdown": [{"label": "Digital", "count": 1}],
    }
    assert sum(row["count"] for row in trashed["recently_added"]) == 1

    await _restore(library)
    assert await _overview(library) == before


async def test_weekly_digest_drops_trashed_game_activity_and_restore_brings_it_back(library):
    before = await _digest(library)
    assert before == {
        "games_played": 2,
        "games_added": 1,
        "achievements_unlocked": 3,
        "metadata_changes": 2,
    }

    await _trash(library)
    assert await _digest(library) == {
        "games_played": 1,
        "games_added": 0,
        "achievements_unlocked": 1,
        "metadata_changes": 1,
    }

    await _restore(library)
    # trash and restore record no activity of their own: the numbers are the same
    assert await _digest(library) == before


async def test_library_with_only_trashed_games_keeps_empty_defaults(library):
    async with SessionLocal() as db:
        await db.execute(
            update(Game).where(Game.user_id == library.user_id).values(deleted_at=int(time.time()))
        )
        await db.commit()
    overview = await _overview(library)
    assert library.measured == []
    assert overview == {
        "total_games": 0,
        "favorite_count": 0,
        "total_playtime_seconds": 0,
        "storage_used_bytes": 0,
        "total_spent": 0.0,
        "average_rating": None,
        "status_breakdown": [],
        "source_breakdown": [],
        "most_played": [],
        "recently_added": [],
        "rating_histogram": [],
        "top_tags": [],
        "release_year_breakdown": [],
        "format_breakdown": [],
    }
    assert await _digest(library) == {
        "games_played": 0,
        "games_added": 0,
        "achievements_unlocked": 0,
        "metadata_changes": 0,
    }


async def test_media_statistics_stay_scoped_while_a_game_is_trashed(library):
    async with SessionLocal() as db:
        db.add_all(
            [
                Movie(
                    user_id=library.user_id,
                    title="Watched",
                    sort_title="watched",
                    status=MovieStatus.WATCHED,
                    runtime_minutes=95,
                ),
                Movie(
                    user_id=library.user_id,
                    title="Trashed movie",
                    sort_title="trashed movie",
                    status=MovieStatus.WATCHED,
                    runtime_minutes=500,
                    deleted_at=1,
                ),
            ]
        )
        await db.commit()

    async def media_stats():
        response = await library.client.get("/api/media-stats")
        assert response.status_code == 200, response.text
        return response.json()

    before = await media_stats()
    assert before["movie"]["titles"] == 1
    assert before["movie"]["minutes_watched"] == 95
    assert before["games"]["titles"] == 2
    assert before["games"]["achievements_unlocked"] == 4
    await _trash(library)
    trashed = await media_stats()
    assert trashed["movie"] == before["movie"]
    assert trashed["games"]["titles"] == 1
    assert trashed["games"]["playtime_seconds"] == 3600
    assert trashed["games"]["achievements_unlocked"] == 2
    assert trashed["overview"]["game_favorites"] == 1
    # the Statistics games tab and the overview endpoint agree on the active library
    overview = await _overview(library)
    assert trashed["games"]["titles"] == overview["total_games"]
    assert trashed["games"]["playtime_seconds"] == overview["total_playtime_seconds"]
    await _restore(library)
    restored = await media_stats()
    for key in ("titles", "favorites", "score", "playtime_seconds", "achievements_unlocked"):
        assert restored["games"][key] == before["games"][key], key
    assert restored["movie"] == before["movie"]


async def test_each_account_counts_only_its_own_active_games(library):
    await _trash(library)
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=library.stranger_id)
    try:
        overview = await _overview(library)
        digest = await _digest(library)
    finally:
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=library.user_id)
    assert library.measured == ["foreign"]
    assert {key: overview[key] for key in _TOTALS} == {
        "total_games": 1,
        "favorite_count": 1,
        "total_playtime_seconds": 999_999,
        "storage_used_bytes": 1_000_000,
        "total_spent": 99.99,
        "average_rating": 1.0,
    }
    assert overview["top_tags"] == [{"label": "Foreign", "count": 1}]
    assert digest == {
        "games_played": 1,
        "games_added": 1,
        "achievements_unlocked": 1,
        "metadata_changes": 1,
    }
