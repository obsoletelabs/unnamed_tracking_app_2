"""The game list stays a plain page array and reports the matching total."""

from types import SimpleNamespace

from fastapi import Response

from src.api.routes import games
from src.api.schemas.game import GameCreate
from src.database.session import SessionLocal
from tests.test_game_files_flow import game_flow  # noqa: F401


async def _add_games(flow, count):
    async with SessionLocal() as db:
        for index in range(count):
            await games.create_game(
                GameCreate(title=f"Total {index}", folder_location=f"Total{index}"),
                Response(),
                db,
                SimpleNamespace(id=flow.user_id),
            )


async def test_total_header_counts_all_matches_regardless_of_page(flow):
    await _add_games(flow, 4)
    first = await flow.client.get("/api/game/list?skip=0&limit=2")
    assert first.status_code == 200
    assert isinstance(first.json(), list) and len(first.json()) == 2
    assert first.headers["x-total-count"] == "5"
    last = await flow.client.get("/api/game/list?skip=4&limit=2")
    assert len(last.json()) == 1
    assert last.headers["x-total-count"] == "5"


async def test_total_header_follows_filters_and_hides_other_accounts(flow):
    await _add_games(flow, 2)
    found = await flow.client.get("/api/game/list?search=Total%201")
    assert [game["title"] for game in found.json()] == ["Total 1"]
    assert found.headers["x-total-count"] == "1"
    none = await flow.client.get("/api/game/list?search=nothing-matches")
    assert none.json() == [] and none.headers["x-total-count"] == "0"


async def test_status_page_is_narrow_but_tab_counts_keep_the_search_scope(flow):
    await _add_games(flow, 4)
    response = await flow.client.get("/api/game/list?search=Total&status=BACKLOG&limit=2")
    assert response.status_code == 200
    assert len(response.json()) == 2
    assert response.headers["x-total-count"] == "4"
    counts = dict(item.split(":") for item in response.headers["x-status-counts"].split(","))
    assert counts["backlog"] == "4"


async def test_collection_index_only_returns_membership_fields(flow):
    async with SessionLocal() as db:
        game = await games.create_game(
            GameCreate(
                title="Collection index title",
                folder_location="CollectionIndexTitle",
                description="Long detail page text",
                collections=["Favorites shelf"],
                tags=["Puzzle"],
                favorite=True,
                playtime_seconds=7200,
            ),
            Response(),
            db,
            SimpleNamespace(id=flow.user_id),
        )
        game_id = str(game.id)
    response = await flow.client.get("/api/game/collection-index")
    assert response.status_code == 200
    entry = next(item for item in response.json() if item["id"] == game_id)
    assert entry == {
        "id": game_id,
        "title": "Collection index title",
        "status": "BACKLOG",
        "favorite": True,
        "source": None,
        "tags": ["Puzzle"],
        "collections": ["Favorites shelf"],
        "playtime_seconds": 7200,
    }
