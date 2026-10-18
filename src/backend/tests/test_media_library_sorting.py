"""Media sort order is global, deterministic and shared with rank and progress."""

from datetime import date
from decimal import Decimal
from uuid import UUID

import pytest

from src.database.models.anime import Anime, AnimeSeason
from src.database.models.movies import Movie
from src.database.models.tv_show import TVSeason, TVShow
from src.database.session import SessionLocal
from tests.test_game_files_flow import game_flow  # noqa: F401


@pytest.mark.parametrize(
    ("kind", "model", "season_model"),
    [
        ("movie", Movie, None),
        ("tv", TVShow, TVSeason),
        ("anime", Anime, AnimeSeason),
    ],
)
async def test_global_sorting_has_stable_ties_and_unknowns_last(flow, kind, model, season_model):
    ids = {number: UUID(int=number) for number in range(1, 6)}
    async with SessionLocal() as db:
        for number, title, rating, year, added, state in [
            (2, "Zulu", "9", 2020, 20, "WATCHLIST"),
            (1, "Zulu", "9", 2020, 20, "WISHLIST"),
            (3, "Alpha", "2", 2010, 10, "WATCHED"),
            (4, "No estimates", None, None, 30, "BACKLOG"),
            (5, "Deleted", "10", 2025, 40, "WATCHED"),
        ]:
            row = model(
                id=ids[number],
                user_id=flow.user_id,
                title=title,
                sort_title=title.lower(),
                rating_overall=Decimal(rating) if rating else None,
                created_at=added,
                status=state,
                deleted_at=1 if number == 5 else None,
            )
            setattr(
                row,
                "release_date" if kind == "movie" else "first_air_date",
                date(year, 1, 1) if year else None,
            )
            db.add(row)
        await db.flush()
        if season_model:
            db.add_all(
                [
                    season_model(
                        show_id=ids[1], season_number=1, episode_count=10, episodes_watched=9
                    ),
                    season_model(
                        show_id=ids[2], season_number=1, episode_count=None, episodes_watched=1
                    ),
                    season_model(
                        show_id=ids[3], season_number=1, episode_count=4, episodes_watched=2
                    ),
                ]
            )
        await db.commit()

    expected = {
        "title": [3, 4, 1, 2],
        "title_desc": [1, 2, 4, 3],
        "score": [1, 2, 3, 4],
        "rank": [1, 2, 3, 4],
        "added": [4, 1, 2, 3],
        "year_new": [1, 2, 3, 4],
        "year_old": [3, 1, 2, 4],
        "progress": [3, 4, 1, 2] if kind == "movie" else [1, 3, 4, 2],
    }
    for sort, numbers in expected.items():
        pages = []
        for offset in (0, 2):
            response = await flow.client.get(
                f"/api/{kind}/list", params={"sort": sort, "skip": offset, "limit": 2}
            )
            assert response.status_code == 200, response.text
            page = response.json()
            assert page["total"] == 4
            assert page["score_ranks"] == {str(ids[1]): 1, str(ids[2]): 2, str(ids[3]): 3}
            pages.extend(item["id"] for item in page["items"])
        assert pages == [str(ids[number]) for number in numbers], sort
    assert (
        await flow.client.get(f"/api/{kind}/list", params={"sort": "unknown"})
    ).status_code == 422


@pytest.mark.parametrize(("kind", "model"), [("movie", Movie), ("tv", TVShow), ("anime", Anime)])
async def test_best_match_orders_search_results_by_how_closely_titles_match(flow, kind, model):
    """#245: typing a search can rank results by match quality instead of the chosen ordering."""
    titles = {
        1: "The Last Hunt",  # contains the text
        2: "Hunt",  # exact
        3: "Hunters of the Dawn",  # starts with it
        4: "A Hunt for Red October",  # a word starts with it
        5: "Unrelated",  # not a match at all
        6: "Manhunt",  # contains it inside a word
    }
    async with SessionLocal() as db:
        for number, title in titles.items():
            db.add(
                model(
                    id=UUID(int=number),
                    user_id=flow.user_id,
                    title=title,
                    sort_title=title.lower(),
                    created_at=number,
                    status="WATCHLIST",
                )
            )
        await db.commit()

    async def order(sort: str, search: str = "hunt") -> list[int]:
        response = await flow.client.get(
            f"/api/{kind}/list", params={"sort": sort, "search": search}
        )
        assert response.status_code == 200, response.text
        return [UUID(item["id"]).int for item in response.json()["items"]]

    assert await order("relevance") == [2, 3, 4, 1, 6]
    assert await order("title") == [4, 2, 3, 6, 1]  # the chosen ordering is unchanged
    assert await order("relevance", "100%") == []  # wildcard characters match themselves
    # without a search the option falls back to the ordinary title order
    response = await flow.client.get(f"/api/{kind}/list", params={"sort": "relevance"})
    assert [item["title"] for item in response.json()["items"]] == sorted(
        titles.values(), key=str.lower
    )
