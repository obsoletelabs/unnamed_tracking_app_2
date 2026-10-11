"""Statistics tabs preserve exact numbers without querying unrelated libraries."""

from uuid import uuid4

import httpx
import pytest
from sqlalchemy import event

from src.api.routes.media_stats import get_media_stats
from src.core.auth import get_current_user
from src.database.models.achievement import Achievement
from src.database.models.anime import Anime, AnimeEpisode, AnimeSeason, AnimeStatus
from src.database.models.game import Game, GameStatus
from src.database.models.movies import Movie, MovieStatus
from src.database.models.tv_show import TVEpisode, TVSeason, TVShow, TVShowStatus
from src.database.models.user import User
from src.database.session import SessionLocal
from src.main import app


@pytest.fixture
async def stats_library():
    async with SessionLocal() as db:
        user = User(username=uuid4().hex, email=f"{uuid4().hex}@example.test", password_hash="x")
        other = User(username=uuid4().hex, email=f"{uuid4().hex}@example.test", password_hash="x")
        db.add_all([user, other])
        await db.flush()
        game = Game(
            user_id=user.id,
            title="Played",
            sort_title="played",
            folder_location=uuid4().hex,
            status=GameStatus.BEATEN,
            playtime_seconds=7200,
            favorite=True,
        )
        movie = Movie(
            user_id=user.id,
            title="Watched",
            sort_title="watched",
            status=MovieStatus.WATCHED,
            runtime_minutes=100,
            rewatches=1,
        )
        tv = TVShow(user_id=user.id, title="TV", sort_title="tv", status=TVShowStatus.IN_PROGRESS)
        anime = Anime(
            user_id=user.id, title="Anime", sort_title="anime", status=AnimeStatus.WATCHED
        )
        db.add_all(
            [
                game,
                movie,
                tv,
                anime,
                Movie(
                    user_id=other.id,
                    title="Other",
                    sort_title="other",
                    status=MovieStatus.WATCHED,
                    runtime_minutes=999,
                ),
                Movie(
                    user_id=user.id,
                    title="Trashed",
                    sort_title="trashed",
                    deleted_at=1,
                    status=MovieStatus.WATCHED,
                    runtime_minutes=999,
                ),
            ]
        )
        await db.flush()
        tv_season = TVSeason(show_id=tv.id, season_number=1, episode_count=1, episodes_watched=1)
        anime_season = AnimeSeason(
            show_id=anime.id, season_number=1, episode_count=1, episodes_watched=1
        )
        db.add_all([tv_season, anime_season])
        await db.flush()
        db.add_all(
            [
                TVEpisode(
                    season_id=tv_season.id, episode_number=1, runtime_minutes=42, watched=True
                ),
                AnimeEpisode(
                    season_id=anime_season.id, episode_number=1, runtime_minutes=24, watched=True
                ),
                Achievement(
                    game_id=game.id, provider="Steam", external_id="1", name="Done", unlocked=True
                ),
            ]
        )
        await db.flush()
        yield db, user
        await db.rollback()


@pytest.mark.parametrize(
    "section,unrelated",
    [
        ("games", ("movies", "tv_shows", "tv_episodes", "anime", "anime_episodes", "activity_log")),
        (
            "movie",
            (
                "games",
                "achievements",
                "tv_shows",
                "tv_episodes",
                "anime",
                "anime_episodes",
                "activity_log",
            ),
        ),
        ("tv", ("games", "achievements", "movies", "anime", "anime_episodes", "activity_log")),
        ("anime", ("games", "achievements", "movies", "tv_shows", "tv_episodes", "activity_log")),
    ],
)
async def test_selected_section_matches_full_response_and_skips_other_libraries(
    stats_library, section, unrelated
):
    db, user = stats_library
    full = await get_media_stats(db, user)
    statements = []
    connection = await db.connection()

    def record(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement.lower())

    event.listen(connection.sync_connection, "before_cursor_execute", record)
    try:
        selected = await get_media_stats(db, user, section=section)
    finally:
        event.remove(connection.sync_connection, "before_cursor_execute", record)
    assert selected == {section: full[section]}
    for table in unrelated:
        assert not any(
            f"from {table} " in sql or f"join {table} " in sql or f"from {table}\n" in sql
            for sql in statements
        ), table
    assert len(statements) < 10


async def test_overview_is_self_contained_and_scoped_to_active_user_titles(stats_library):
    db, user = stats_library
    full = await get_media_stats(db, user)
    selected = await get_media_stats(db, user, section="overview")
    assert selected == {"overview": full["overview"]}
    assert selected["overview"]["game_favorites"] == 1
    assert selected["overview"]["achievements_unlocked"] == 1
    assert selected["overview"]["media_minutes"] == 266
    assert full["movie"]["titles"] == 1
    assert set(full) == {"overview", "games", "movie", "tv", "anime"}


async def test_unknown_section_is_rejected_at_api_boundary(stats_library):
    _, user = stats_library
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/media-stats?section=unknown")
        assert response.status_code == 422
    finally:
        app.dependency_overrides.pop(get_current_user, None)
