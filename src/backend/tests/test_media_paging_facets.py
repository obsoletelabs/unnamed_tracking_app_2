"""Later media pages omit library-wide facet and leaderboard queries."""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from src.api.routes import media_common
from src.database.models.movies import Movie


async def test_later_page_skips_facets_and_ranks(monkeypatch):
    db = AsyncMock()
    db.scalar.return_value = 120
    result = MagicMock()
    result.scalars.return_value.unique.return_value.all.return_value = []
    db.execute.return_value = result
    ranks = AsyncMock()
    monkeypatch.setattr(media_common, "score_ranks", ranks)

    page = await media_common.library_page(
        db,
        Movie,
        uuid4(),
        filters=media_common.LibraryFilters(),
        skip=48,
        limit=48,
        include_facets=False,
    )

    assert page.total == 120
    assert page.status_counts == {}
    assert page.score_ranks == {}
    db.execute.assert_awaited_once()
    ranks.assert_not_awaited()
