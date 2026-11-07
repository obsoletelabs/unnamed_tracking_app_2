"""PlayStation and RetroAchievements syncs record who supplied each field of a new game (#348).

A sync enriches its new games concurrently, outside the import's session, and then records each
game's field sources in that session. These tests drive the real sync routes with the store
clients and the metadata lookup replaced, and read the field sources back through the API."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from src.api.routes import library_sync
from src.core.crypto import encrypt_secret
from src.database.models.game import Game
from src.database.models.user import User
from src.database.session import SessionLocal
from src.helpers import save_game_asset
from tests.test_game_files_flow import game_flow  # noqa: F401

# two new games, each merged from different providers; the first answers last
RECORDS = {
    "Portal 2": {
        "title": "Portal 2",
        "provider_name": "Steam",
        "metadata": {
            "description": "Portal from Steam",
            "developer": "Valve",
            "tags": ["Puzzle"],
            "first_release": {"date": "2011-04", "precision": "month"},
        },
        "field_providers": {
            "description": "Steam",
            "developer": "IGDB",
            "tags": "IGDB",
            "first_release": "Atlas",
        },
        "assets": [],
    },
    "Celeste": {
        "title": "Celeste",
        "provider_name": "IGDB",
        "metadata": {
            "description": "Celeste from RAWG",
            "publisher": "Maddy Makes Games",
            "series": "Celeste",
        },
        "field_providers": {
            "description": "RAWG",
            "publisher": "IGDB",
            "series": "Atlas",
        },
        "assets": [],
    },
}

EXPECTED = {
    "Portal 2": {
        "description": "Steam",
        "developer": "IGDB",
        "tags": "IGDB",
        "release_date": "Atlas",
    },
    "Celeste": {"description": "RAWG", "publisher": "IGDB", "series": "Atlas"},
}


@pytest.fixture(autouse=True)
def isolated_import_storage(tmp_path, monkeypatch):
    monkeypatch.setattr(save_game_asset, "DATA_ROOT", tmp_path)


@pytest.fixture
def merged_lookup(monkeypatch):
    """The metadata lookup answers each new game with its merged record; Portal 2 is slow, so
    the enrichments finish out of order."""

    async def resolve(_db, _user_id, candidate, **_kwargs):
        if candidate.title == "Portal 2":
            await asyncio.sleep(0.05)
        return RECORDS.get(candidate.title), []

    lookup = AsyncMock(side_effect=resolve)
    monkeypatch.setattr(library_sync, "resolve_library_record", lookup)
    return lookup


async def connect(flow) -> None:
    async with SessionLocal() as db:
        user = await db.get(User, flow.user_id)
        user.retroachievements_username, user.retroachievements_api_key = "player", "key"
        user.psn_npsso_token = encrypt_secret("token")
        await db.commit()


async def sync(flow, route: str) -> dict:
    """The sync route as the app calls it, for the signed-in owner."""
    handler = {
        "psn": library_sync.sync_psn_library,
        "retroachievements": library_sync.sync_retroachievements_library,
    }[route]
    async with SessionLocal() as db:
        return await handler(db, await db.get(User, flow.user_id))


def store_library(monkeypatch, source: str) -> None:
    if source == "RetroAchievements":
        monkeypatch.setattr(
            library_sync.RetroAchievementsClient,
            "get_user_games",
            lambda *_: [{"GameID": 501, "Title": "Portal 2"}, {"GameID": 502, "Title": "Celeste"}],
        )
        monkeypatch.setattr(
            library_sync.RetroAchievementsClient, "get_game_progress", lambda *_: {}
        )
    else:
        monkeypatch.setattr(
            library_sync.PSNClient,
            "get_owned_titles",
            lambda *_: [
                {"npCommunicationId": "NPWR501", "trophyTitleName": "Portal 2"},
                {"npCommunicationId": "NPWR502", "trophyTitleName": "Celeste"},
            ],
        )
        monkeypatch.setattr(library_sync.PSNClient, "get_trophies_for_title", lambda *_: [])


async def games_by_title(user_id, source: str) -> dict[str, Game]:
    async with SessionLocal() as db:
        rows = await db.scalars(select(Game).where(Game.user_id == user_id, Game.source == source))
        games = {game.title: game for game in rows}
        for game in games.values():
            db.expunge(game)
        return games


@pytest.mark.parametrize(
    ("source", "route"), [("PlayStation", "psn"), ("RetroAchievements", "retroachievements")]
)
async def test_a_store_sync_records_each_new_games_field_sources(
    flow, monkeypatch, merged_lookup, source, route
):
    await connect(flow)
    store_library(monkeypatch, source)

    result = await sync(flow, route)

    assert result["games_added"] == 2
    assert merged_lookup.await_count == 2
    games = await games_by_title(flow.user_id, source)
    assert set(games) == {"Portal 2", "Celeste"}
    for title, expected in EXPECTED.items():
        sources = (await flow.client.get(f"/api/game/{games[title].id}/field-sources")).json()
        # each game's own record, although the two enrichments finished in the other order
        assert {field: entry["provider"] for field, entry in sources.items()} == expected
        assert {entry["origin"] for entry in sources.values()} == {"metadata"}

    portal = games["Portal 2"]
    assert (portal.description, portal.developer) == ("Portal from Steam", "Valve")
    assert portal.release_date_precision == "month"
    # the source tag the sync adds stays next to the provider's tags; the provenance holds
    # what the provider reported
    assert portal.tags == ["Puzzle", source]
    sources = (await flow.client.get(f"/api/game/{portal.id}/field-sources")).json()
    assert [entry["value"] for entry in sources["tags"]["sources"]] == [["Puzzle"]]
    assert [entry["value"] for entry in sources["release_date"]["sources"]] == ["2011-04"]
    assert games["Celeste"].tags == [source]


@pytest.mark.parametrize(
    ("source", "route"), [("PlayStation", "psn"), ("RetroAchievements", "retroachievements")]
)
async def test_a_second_store_sync_records_nothing_new(
    flow, monkeypatch, merged_lookup, source, route
):
    await connect(flow)
    store_library(monkeypatch, source)
    await sync(flow, route)
    games = await games_by_title(flow.user_id, source)
    first = {
        title: (await flow.client.get(f"/api/game/{game.id}/field-sources")).json()
        for title, game in games.items()
    }

    again = await sync(flow, route)

    assert again["games_added"] == 0
    # existing games are not enriched again, so their sources stay as recorded
    assert merged_lookup.await_count == 2
    for title, game in games.items():
        assert (await flow.client.get(f"/api/game/{game.id}/field-sources")).json() == first[title]
