"""Reproducible query profile of the movie, TV, anime, list, session and invitation reads (#260).

The game identity paths were measured in #103/#205 (`test_game_identity_scale.py`). This file
covers the remaining media and account reads against a generated library: it counts SQL
statements and the rows the ORM materialises for each request, and ceilings guard against
regression to per-row queries. Set `PROFILE_REPORT=1` (and optionally `PROFILE_MOVIES`,
`PROFILE_SHOWS`, `PROFILE_ANIME`) to print the statement counts and EXPLAIN (ANALYZE) plans that
are recorded in wiki/docs/development/media-query-profile.md:

    PROFILE_REPORT=1 pytest tests/test_media_query_profile.py -s
"""

import os
import time
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import event, insert, select

from src.core.auth import get_current_admin, get_current_user
from src.core.crypto import encrypt_secret
from src.database.models.anime import Anime, AnimeEpisode, AnimeSeason
from src.database.models.auth import UserSession
from src.database.models.media_extras import MediaList, MediaListItem, MediaType
from src.database.models.movies import Movie
from src.database.models.tv_show import TVEpisode, TVSeason, TVShow
from src.database.models.user import User
from src.database.models.user_invitation import NotificationPendingRecipient, UserInvitation
from src.database.session import SessionLocal, engine
from src.main import app

REPORT = os.environ.get("PROFILE_REPORT") == "1"
MOVIES = int(os.environ.get("PROFILE_MOVIES", "5000"))
SHOWS = int(os.environ.get("PROFILE_SHOWS", "1500"))
ANIME = int(os.environ.get("PROFILE_ANIME", "1000"))
SEASONS, EPISODES = 3, 10
SESSIONS = 300
LIST_ITEMS = 40
STATUSES = ["WISHLIST", "WATCHLIST", "BACKLOG", "IN_PROGRESS", "WATCHED", "FAVORITE", "DROPPED"]


class Recorder:
    """Statements (with parameters) and ORM rows loaded while a request runs."""

    def __init__(self):
        self.statements: list[tuple[str, object]] = []
        self.rows = 0

    def _hit(self, _conn, _cursor, statement, parameters, _context, _many):
        self.statements.append((statement, parameters))

    def _loaded(self, *_args):
        self.rows += 1

    def __enter__(self):
        event.listen(engine.sync_engine, "before_cursor_execute", self._hit)
        for model in (Movie, TVShow, TVSeason, TVEpisode, Anime, AnimeSeason, AnimeEpisode):
            event.listen(model, "load", self._loaded)
        return self

    def __exit__(self, *_exc):
        event.remove(engine.sync_engine, "before_cursor_execute", self._hit)
        for model in (Movie, TVShow, TVSeason, TVEpisode, Anime, AnimeSeason, AnimeEpisode):
            event.remove(model, "load", self._loaded)

    @property
    def count(self) -> int:
        return len(self.statements)


def _media_rows(user_id, count, prefix, extra=None):
    rows = []
    for index in range(count):
        title = f"{prefix} {index:05d}"
        row = {
            "id": uuid4(),
            "user_id": user_id,
            "title": title,
            "sort_title": title.lower(),
            "status": STATUSES[index % len(STATUSES)],
            "genres": ["Drama", "Comedy"] if index % 3 else ["Action"],
            "rating_overall": (index % 10) + 0.5 if index % 4 else None,
            "favorite": index % 11 == 0,
        }
        rows.append({**row, **(extra or {})})
    return rows


async def _seed_library(db, user_id):
    await db.execute(insert(Movie), _media_rows(user_id, MOVIES, "Movie"))
    for model, season_model, episode_model, count, prefix in (
        (TVShow, TVSeason, TVEpisode, SHOWS, "Show"),
        (Anime, AnimeSeason, AnimeEpisode, ANIME, "Anime"),
    ):
        shows = _media_rows(user_id, count, prefix)
        await db.execute(insert(model), shows)
        seasons, episodes = [], []
        for show in shows:
            for number in range(1, SEASONS + 1):
                season_id = uuid4()
                seasons.append(
                    {
                        "id": season_id,
                        "show_id": show["id"],
                        "season_number": number,
                        "episode_count": EPISODES,
                        "episodes_watched": number,
                    }
                )
                episodes.extend(
                    {
                        "id": uuid4(),
                        "season_id": season_id,
                        "episode_number": episode,
                        "watched": episode <= number,
                    }
                    for episode in range(1, EPISODES + 1)
                )
        await db.execute(insert(season_model), seasons)
        for start in range(0, len(episodes), 10000):
            await db.execute(insert(episode_model), episodes[start : start + 10000])


@pytest.fixture
async def profile_data():
    async with SessionLocal() as db:
        users = [
            User(username=uuid4().hex, email=f"{uuid4().hex}@example.test", password_hash="x")
            for _ in range(2)
        ]
        users[0].is_admin = True
        db.add_all(users)
        await db.flush()
        owner, other = users
        for account in users:  # another account holds a same-sized library
            await _seed_library(db, account.id)
        now = int(time.time())
        await db.execute(
            insert(UserSession),
            [
                {
                    "id": uuid4(),
                    "user_id": users[index % 2].id,
                    "token_hash": uuid4().hex + uuid4().hex,
                    "expires_at": now + 3600 + index,
                    "last_seen_at": now - index,
                    "revoked_at": now if index % 5 == 0 else None,
                }
                for index in range(SESSIONS)
            ],
        )
        recipients = [{"id": uuid4(), "expires_at": now + 86400} for _ in range(100)]
        await db.execute(insert(NotificationPendingRecipient), recipients)
        await db.execute(
            insert(UserInvitation),
            [
                {
                    "id": uuid4(),
                    "pending_recipient_id": recipient["id"],
                    "issued_by": owner.id,
                    "plugin_id": "profile",
                    "installation_id": uuid4(),
                    "flow_revision": 1,
                    "email_digest": uuid4().hex,
                    "encrypted_email": encrypt_secret("invitee@example.test"),
                    "token_hash": uuid4().hex,
                    "app_url": "http://test",
                    "created_at": now - index,
                    "expires_at": now + 86400,
                }
                for index, recipient in enumerate(recipients)
            ],
        )
        movie_ids = list(
            await db.scalars(select(Movie.id).where(Movie.user_id == owner.id).limit(LIST_ITEMS))
        )
        manual = MediaList(user_id=owner.id, name="Manual", position=1)
        smart = MediaList(
            user_id=owner.id, name="Smart", position=2, smart_rule={"status_buckets": ["completed"]}
        )
        db.add_all([manual, smart])
        await db.flush()
        await db.execute(
            insert(MediaListItem),
            [
                {
                    "id": uuid4(),
                    "list_id": manual.id,
                    "media_type": MediaType.MOVIE,
                    "media_id": movie_id,
                    "position": index,
                    "added_at": now,
                }
                for index, movie_id in enumerate(movie_ids)
            ],
        )
        await db.commit()
        # Autovacuum would have analysed a table this size; plans must see real statistics.
        raw = await db.connection()
        for table in (
            "movies",
            "tv_shows",
            "tv_seasons",
            "tv_episodes",
            "anime",
            "anime_seasons",
            "anime_episodes",
            "user_sessions",
            "media_lists",
            "media_list_items",
        ):
            await raw.exec_driver_sql(f"ANALYZE {table}")
        await db.commit()
        owner_id, other_id = owner.id, other.id
    async with SessionLocal() as db:
        owner = await db.get(User, owner_id)
        db.expunge(owner)
    yield owner, other_id
    async with SessionLocal() as db:
        for account_id in (owner_id, other_id):
            await db.delete(await db.get(User, account_id))
        await db.commit()


async def _get(owner, path):
    app.dependency_overrides[get_current_user] = lambda: owner
    app.dependency_overrides[get_current_admin] = lambda: owner
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            with Recorder() as recorder:
                response = await client.get(path)
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_current_admin, None)
    assert response.status_code == 200, response.text
    return response.json(), recorder


REQUESTS = {
    "movie list (default)": "/api/movie/list?limit=50",
    "movie list (search)": "/api/movie/list?limit=50&search=Movie%2000042",
    "movie list (status+genre+score sort)": (
        "/api/movie/list?limit=50&status_bucket=completed&genre=Drama&sort=score"
    ),
    "movie list (year sort)": "/api/movie/list?limit=50&sort=year_new",
    "tv list (default)": "/api/tv/list?limit=50",
    "tv list (progress sort)": "/api/tv/list?limit=50&sort=progress",
    "anime list (default)": "/api/anime/list?limit=50",
    "anime list (search)": "/api/anime/list?limit=50&search=Anime%2000042",
    "anime list (progress sort)": "/api/anime/list?limit=50&sort=progress",
    "custom lists (manual + smart)": "/api/lists",
    "one manual list": "/api/lists/{manual}",
    "own sessions": "/api/sessions/me",
    "admin sessions": "/api/sessions/admin",
    "pending invitations": "/api/auth/user-invitations",
}

# Statement and ORM-row ceilings (after the fixes in this issue). A request must not issue a
# statement per row or load the library as ORM objects to answer one page.
CEILINGS = {
    "movie list (default)": (8, 60),
    "movie list (search)": (8, 60),
    "movie list (status+genre+score sort)": (8, 60),
    "movie list (year sort)": (8, 60),
    "tv list (default)": (10, 400),
    "tv list (progress sort)": (10, 400),
    "anime list (default)": (10, 400),
    "anime list (search)": (10, 400),
    "anime list (progress sort)": (10, 400),
    "custom lists (manual + smart)": (16, 6_000),
    "one manual list": (8, 100),
    "own sessions": (4, 0),
    "admin sessions": (6, 0),
    "pending invitations": (4, 0),
}


async def _explain(recorder):
    plans = []
    async with engine.connect() as connection:
        for statement, parameters in recorder.statements:
            if not statement.lstrip().upper().startswith("SELECT"):
                continue
            rows = await connection.exec_driver_sql("EXPLAIN (ANALYZE) " + statement, parameters)
            plan = [row[0] for row in rows]
            plans.append((statement, plan))
    return plans


async def test_media_and_account_reads_stay_bounded(profile_data):
    owner, _ = profile_data
    async with SessionLocal() as db:
        manual_id = await db.scalar(
            select(MediaList.id).where(MediaList.user_id == owner.id, MediaList.name == "Manual")
        )
    over = []
    for label, path in REQUESTS.items():
        started = time.perf_counter()
        body, recorder = await _get(owner, path.replace("{manual}", str(manual_id)))
        elapsed = (time.perf_counter() - started) * 1000
        statements, rows = CEILINGS[label]
        if REPORT:
            print(
                f"\n## {label}: {recorder.count} statements, {recorder.rows} ORM rows, "
                f"{elapsed:.0f} ms"
            )
            for statement, plan in await _explain(recorder):
                print("  SQL:", " ".join(statement.split())[:240])
                for line in plan:
                    print("    ", line)
        assert body is not None
        if recorder.count > statements or recorder.rows > rows:
            over.append((label, recorder.count, recorder.rows))
    assert not over, over


async def test_lists_and_library_cards_keep_their_semantics(profile_data):
    """The query changes must not change which titles a list holds or what a card shows."""
    owner, _ = profile_data
    async with SessionLocal() as db:
        owned = (
            await db.scalars(
                select(Movie).where(Movie.user_id == owner.id, Movie.deleted_at.is_(None))
            )
        ).all()
        db.add(
            MediaList(
                user_id=owner.id,
                name="Rule",
                position=3,
                smart_rule={
                    "media_types": ["movie"],
                    "status_buckets": ["completed"],
                    "genre": "action",
                    "min_score": 5,
                    "favorite": True,
                },
            )
        )
        await db.commit()
        expected = sorted(
            movie.id
            for movie in owned
            if movie.status.value in {"WATCHED", "FAVORITE"}
            and "action" in [genre.lower() for genre in movie.genres]
            and movie.rating_overall is not None
            and float(movie.rating_overall) >= 5
            and movie.favorite
        )
    body, _ = await _get(owner, "/api/lists")
    rule = next(entry for entry in body if entry["name"] == "Rule")
    assert rule["item_count"] == len(expected) > 0
    detail, _ = await _get(owner, f"/api/lists/{rule['id']}")
    assert sorted(item["media_id"] for item in detail["items"]) == [str(i) for i in expected]
    manual = next(entry for entry in body if entry["name"] == "Manual")
    assert manual["item_count"] == LIST_ITEMS
    tv, _ = await _get(owner, "/api/tv/list?limit=5")
    assert tv["total"] == SHOWS
    assert all(len(show["seasons"]) == SEASONS for show in tv["items"])
    assert all("episodes" not in season for show in tv["items"] for season in show["seasons"])
