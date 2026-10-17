"""Statistics activity days: the heatmap's counts and the day detail always agree.

Episode and history entries stay on the calendar day the history log recorded;
achievements land on the local day of their unlock instant in the requested
timezone. Both views use the same rule and the same ownership joins, so another
account's rows, trashed or purged titles and trashed games never count or link,
and nothing without a recorded date is placed on a day."""

import time
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx
import pytest
from sqlalchemy import delete, select, update

from src.core.auth import get_current_user
from src.database.models.achievement import Achievement
from src.database.models.anime import Anime, AnimeStatus
from src.database.models.game import Game, GameStatus
from src.database.models.media_extras import ActivityEventType, ActivityLog, MediaType
from src.database.models.movies import Movie, MovieStatus
from src.database.models.tv_show import TVShow, TVShowStatus
from src.database.models.user import User
from src.database.session import SessionLocal
from src.features.activity_stats import (
    ActivityTimezoneError,
    day_bounds,
    heatmap_window,
    local_day,
    resolve_timezone,
)
from src.main import app

# A day-line pair (+14 and -11), half-hour and 45-minute offsets, a zone whose
# daylight saving starts at midnight, and two ordinary DST zones.
ZONES = (
    "UTC",
    "America/Los_Angeles",
    "Pacific/Kiritimati",
    "Pacific/Pago_Pago",
    "Asia/Kathmandu",
    "Australia/Lord_Howe",
    "America/Santiago",
    "Europe/London",
)
_EPISODES = ActivityEventType.EPISODES_WATCHED
_STATUS = ActivityEventType.STATUS_CHANGED


def _user() -> User:
    return User(username=uuid4().hex, email=f"{uuid4().hex}@example.test", password_hash="x")


def _log(user_id, media_type, media_id, event_type, day, count=1, detail=None) -> ActivityLog:
    return ActivityLog(
        user_id=user_id,
        media_type=media_type,
        media_id=media_id,
        media_title="recorded title",
        event_type=event_type,
        event_date=day,
        count=count,
        detail=detail,
    )


def _achievement(game_id, name, unlocked_at, unlocked=True) -> Achievement:
    return Achievement(
        game_id=game_id,
        provider="Steam",
        external_id=name,
        name=name,
        unlocked=unlocked,
        unlocked_at=unlocked_at,
    )


@pytest.fixture
async def activity():
    """The caller's library around one recorded day R (ten days ago in UTC),
    plus rows that must never count: a trashed show, a trashed game, a purged
    title, a row naming a stranger's show and the stranger's own activity."""
    recorded = datetime.now(ZoneInfo("UTC")).date() - timedelta(days=10)
    midnight = int(
        datetime(recorded.year, recorded.month, recorded.day, tzinfo=ZoneInfo("UTC")).timestamp()
    )
    early = midnight + 6 * 3600 + 1800  # R 06:30 UTC
    late = midnight + 10 * 3600 + 1800  # R 10:30 UTC
    async with SessionLocal() as db:
        me, stranger = _user(), _user()
        db.add_all([me, stranger])
        await db.flush()
        show = TVShow(
            user_id=me.id, title="Show", sort_title="show", status=TVShowStatus.IN_PROGRESS
        )
        anime = Anime(
            user_id=me.id,
            title="Anime",
            sort_title="anime",
            title_english="Anime in English",
            status=AnimeStatus.IN_PROGRESS,
        )
        movie = Movie(user_id=me.id, title="Movie", sort_title="movie", status=MovieStatus.WATCHED)
        trashed = TVShow(
            user_id=me.id,
            title="Trashed",
            sort_title="trashed",
            status=TVShowStatus.IN_PROGRESS,
            deleted_at=int(time.time()),
        )
        foreign = TVShow(
            user_id=stranger.id, title="Foreign", sort_title="foreign", status=TVShowStatus.WATCHED
        )
        game = Game(
            user_id=me.id,
            title="Game",
            sort_title="game",
            folder_location=uuid4().hex,
            status=GameStatus.PLAYING,
        )
        trashed_game = Game(
            user_id=me.id,
            title="Trashed game",
            sort_title="trashed game",
            folder_location=uuid4().hex,
            status=GameStatus.PLAYING,
            deleted_at=int(time.time()),
        )
        foreign_game = Game(
            user_id=stranger.id,
            title="Foreign game",
            sort_title="foreign game",
            folder_location=uuid4().hex,
            status=GameStatus.PLAYING,
        )
        db.add_all([show, anime, movie, trashed, foreign, game, trashed_game, foreign_game])
        await db.flush()
        purged_id = uuid4()
        db.add_all(
            [
                _log(me.id, MediaType.TV, show.id, _EPISODES, recorded, 3),
                _log(me.id, MediaType.ANIME, anime.id, _EPISODES, recorded, 2),
                _log(me.id, MediaType.MOVIE, movie.id, _STATUS, recorded, detail="Watching → Done"),
                _log(me.id, MediaType.ANIME, anime.id, _STATUS, recorded - timedelta(days=3)),
                _log(me.id, MediaType.TV, trashed.id, _EPISODES, recorded, 50),
                _log(me.id, MediaType.TV, trashed.id, _EPISODES, recorded - timedelta(days=5), 9),
                # history rows of this account that name someone else's or a purged title
                _log(me.id, MediaType.TV, foreign.id, _EPISODES, recorded, 40),
                _log(me.id, MediaType.TV, foreign.id, _EPISODES, recorded - timedelta(days=6), 4),
                _log(me.id, MediaType.TV, purged_id, _EPISODES, recorded, 30),
                _log(me.id, MediaType.TV, purged_id, _EPISODES, recorded - timedelta(days=7), 3),
                _log(stranger.id, MediaType.TV, foreign.id, _EPISODES, recorded, 7),
                _achievement(game.id, "Early", early),
                _achievement(game.id, "Late", late),
                # locked, or unlocked with no recorded time: on no day at all
                _achievement(game.id, "Locked", early, unlocked=False),
                _achievement(game.id, "Undated", None),
                _achievement(trashed_game.id, "Trashed", early),
                _achievement(foreign_game.id, "Foreign", early),
            ]
        )
        await db.commit()
        context = SimpleNamespace(
            me=me.id,
            stranger=stranger.id,
            show=show.id,
            anime=anime.id,
            movie=movie.id,
            trashed=trashed.id,
            foreign=foreign.id,
            game=game.id,
            trashed_game=trashed_game.id,
            purged=purged_id,
            recorded=recorded,
            early=early,
            late=late,
        )
    acting = {"id": context.me}
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=acting["id"])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        context.client, context.acting = client, acting
        yield context
    app.dependency_overrides.pop(get_current_user, None)
    async with SessionLocal() as db:
        await db.execute(delete(User).where(User.id.in_([context.me, context.stranger])))
        await db.commit()


async def _heatmap(ctx, zone: str = "UTC") -> dict:
    response = await ctx.client.get("/api/media-stats", params={"section": "overview", "tz": zone})
    assert response.status_code == 200, response.text
    return response.json()["overview"]["activity"]


async def _day(ctx, day, zone: str = "UTC", **params) -> dict:
    response = await ctx.client.get(
        "/api/media-stats/activity/day", params={"date": str(day), "tz": zone, **params}
    )
    assert response.status_code == 200, response.text
    return response.json()


def _by_day(activity: dict) -> dict[date, dict]:
    return {date.fromisoformat(row["date"]): row for row in activity["per_day"]}


@pytest.mark.parametrize("zone", ZONES)
async def test_heatmap_counts_and_day_detail_agree_in_every_timezone(activity, zone):
    heat = await _heatmap(activity, zone)
    tz = ZoneInfo(zone)
    assert heat["timezone"] == zone
    first, last = date.fromisoformat(heat["start"]), date.fromisoformat(heat["end"])
    assert first.weekday() == 6  # Sunday
    assert last in {datetime.now(tz).date(), datetime.now(tz).date() - timedelta(days=1)}
    assert 364 <= (last - first).days <= 370

    early_day = local_day(activity.early, tz)
    late_day = local_day(activity.late, tz)
    expected_achievements = {early_day: 0, late_day: 0}
    expected_achievements[early_day] += 1
    expected_achievements[late_day] += 1
    days = _by_day(heat)
    # recorded days never move; achievements follow the zone's local day
    assert set(days) == {activity.recorded, activity.recorded - timedelta(days=3)} | set(
        expected_achievements
    )
    for day, row in days.items():
        assert row["episodes"] == (5 if day == activity.recorded else 0)
        assert row["achievements"] == expected_achievements.get(day, 0)
        assert row["history"] == (
            1 if day in {activity.recorded, activity.recorded - timedelta(days=3)} else 0
        )
        assert row["count"] == row["episodes"] + row["achievements"]

        detail = await _day(activity, day, zone)
        assert {key: detail[key] for key in row if key != "date"} == {
            key: value for key, value in row.items() if key != "date"
        }
        assert detail["date"] == row["date"] and detail["timezone"] == zone
        items = detail["items"]
        assert sum(item["count"] for item in items if item["kind"] == "episodes") == row["episodes"]
        assert sum(item["kind"] == "achievement" for item in items) == row["achievements"]
        assert sum(item["kind"] == "history" for item in items) == row["history"]
        start, end = detail["starts_at"], detail["ends_at"]
        assert (start, end) == day_bounds(day, tz)
        assert all(
            start <= item["unlocked_at"] < end for item in items if item["kind"] == "achievement"
        )

    busiest = heat["busiest_day"]
    assert busiest["count"] == max(row["count"] for row in days.values())
    assert heat["active_days_total"] == len(days)


async def test_day_detail_lists_only_owned_active_entities_with_links(activity):
    detail = await _day(activity, activity.recorded)
    assert detail["count"] == 7 and detail["episodes"] == 5 and detail["achievements"] == 2
    assert detail["items"] == [
        {
            "kind": "episodes",
            "media_type": "tv",
            "id": str(activity.show),
            "title": "Show",
            "count": 3,
        },
        {
            "kind": "episodes",
            "media_type": "anime",
            "id": str(activity.anime),
            "title": "Anime in English",
            "count": 2,
        },
        {
            "kind": "achievement",
            "id": detail["items"][2]["id"],
            "name": "Early",
            "unlocked_at": activity.early,
            "game_id": str(activity.game),
            "game_title": "Game",
        },
        {
            "kind": "achievement",
            "id": detail["items"][3]["id"],
            "name": "Late",
            "unlocked_at": activity.late,
            "game_id": str(activity.game),
            "game_title": "Game",
        },
        {
            "kind": "history",
            "media_type": "movie",
            "id": str(activity.movie),
            "title": "Movie",
            "count": 1,
            "event_type": "status_changed",
            "detail": "Watching → Done",
        },
    ]
    async with SessionLocal() as db:
        owned = set(
            (
                await db.scalars(select(Achievement.id).join(Game).where(Game.id == activity.game))
            ).all()
        )
    assert {item["id"] for item in detail["items"] if item["kind"] == "achievement"} <= {
        str(i) for i in owned
    }
    leaked = {
        str(activity.trashed),
        str(activity.foreign),
        str(activity.purged),
        str(activity.trashed_game),
    }
    assert not leaked & {item.get("id") for item in detail["items"]}
    assert not leaked & {item.get("game_id") for item in detail["items"]}

    # the stranger sees only their own activity for the same day
    activity.acting["id"] = activity.stranger
    theirs = await _day(activity, activity.recorded)
    assert theirs["count"] == 8 and theirs["episodes"] == 7 and theirs["achievements"] == 1
    assert [(i["kind"], i.get("name") or i["title"]) for i in theirs["items"]] == [
        ("episodes", "Foreign"),
        ("achievement", "Foreign"),
    ]
    assert theirs["items"][0]["id"] == str(activity.foreign)
    assert theirs["items"][1]["game_title"] == "Foreign game"
    theirs_heat = _by_day(await _heatmap(activity))
    assert set(theirs_heat) == {activity.recorded}


async def test_trash_restore_and_purge_change_both_views_together(activity):
    async def recorded_counts() -> tuple[dict, dict]:
        return _by_day(await _heatmap(activity))[activity.recorded], await _day(
            activity, activity.recorded
        )

    async def assert_agree(episodes: int, achievements: int, history: int) -> dict:
        row, detail = await recorded_counts()
        expected = {
            "count": episodes + achievements,
            "episodes": episodes,
            "achievements": achievements,
            "history": history,
        }
        assert {key: row[key] for key in expected} == expected
        assert {key: detail[key] for key in expected} == expected
        return detail

    await assert_agree(5, 2, 1)
    assert (await activity.client.delete(f"/api/tv/delete/{activity.show}")).status_code == 204
    detail = await assert_agree(2, 2, 1)
    assert str(activity.show) not in {item.get("id") for item in detail["items"]}
    assert (await activity.client.post(f"/api/tv/{activity.show}/restore")).status_code == 200
    await assert_agree(5, 2, 1)
    assert (await activity.client.delete(f"/api/movie/delete/{activity.movie}")).status_code == 204
    await assert_agree(5, 2, 0)

    # purging keeps the old history rows, which still never count or link
    assert (await activity.client.delete(f"/api/tv/delete/{activity.show}")).status_code == 204
    assert (await activity.client.delete(f"/api/tv/{activity.show}/purge")).status_code == 204
    async with SessionLocal() as db:
        orphaned = await db.scalar(
            select(ActivityLog.id).where(ActivityLog.media_id == activity.show)
        )
    assert orphaned is not None
    await assert_agree(2, 2, 0)

    async with SessionLocal() as db:
        await db.execute(update(Game).where(Game.id == activity.game).values(deleted_at=1))
        await db.commit()
    detail = await assert_agree(2, 0, 0)
    assert [item["kind"] for item in detail["items"]] == ["episodes"]
    async with SessionLocal() as db:
        await db.execute(update(Game).where(Game.id == activity.game).values(deleted_at=None))
        await db.commit()
    await assert_agree(2, 2, 0)


async def test_day_detail_pages_are_bounded_and_ordered(activity):
    everything = await _day(activity, activity.recorded)
    assert everything["total"] == 5 and everything["complete"] is True
    pages, offset = [], 0
    while True:
        page = await _day(activity, activity.recorded, offset=offset, limit=2)
        assert len(page["items"]) <= 2 and page["total"] == 5 and page["limit"] == 2
        assert page["count"] == everything["count"]  # totals never depend on the page
        pages.append([item["kind"] for item in page["items"]])
        if page["complete"]:
            break
        offset = page["next_offset"]
    assert pages == [["episodes", "episodes"], ["achievement", "achievement"], ["history"]]
    stitched = []
    for offset in (0, 2, 4):
        stitched += (await _day(activity, activity.recorded, offset=offset, limit=2))["items"]
    assert stitched == everything["items"]
    beyond = await _day(activity, activity.recorded, offset=50, limit=2)
    assert beyond["items"] == [] and beyond["complete"] is True and beyond["next_offset"] == 50

    for params in ({"limit": 101}, {"limit": 0}, {"offset": -1}):
        response = await activity.client.get(
            "/api/media-stats/activity/day", params={"date": str(activity.recorded), **params}
        )
        assert response.status_code == 422, params


async def test_missing_history_is_never_placed_on_a_day(activity):
    """A locked achievement or one with no recorded unlock time is on no day;
    episodes are the recorded per-title count, never invented episode rows."""
    for zone in ("UTC", "Pacific/Kiritimati"):
        heat = await _heatmap(activity, zone)
        assert sum(row["achievements"] for row in heat["per_day"]) == 2
        for row in heat["per_day"]:
            for item in (await _day(activity, row["date"], zone))["items"]:
                assert item.get("name") not in {"Locked", "Undated", "Trashed", "Foreign"}
                if item["kind"] == "episodes":
                    assert set(item) == {"kind", "media_type", "id", "title", "count"}


@pytest.mark.parametrize(
    "path,params",
    [
        ("/api/media-stats", {"section": "overview", "tz": "Mars/Olympus_Mons"}),
        ("/api/media-stats/activity/day", {"date": "2026-01-01", "tz": "Mars/Olympus_Mons"}),
        ("/api/media-stats/activity/day", {"date": "2026-01-01", "tz": "../../etc/passwd"}),
        ("/api/media-stats/activity/day", {"date": "2026-01-01", "tz": "localtime"}),
        ("/api/media-stats/activity/day", {"date": "2026-01-01", "tz": "A" * 65}),
        ("/api/media-stats/activity/day", {"date": "2026-02-30"}),
        ("/api/media-stats/activity/day", {"date": "yesterday"}),
        ("/api/media-stats/activity/day", {"date": "1969-12-31"}),
        ("/api/media-stats/activity/day", {"date": "9999-12-31"}),
        ("/api/media-stats/activity/day", {}),
    ],
)
async def test_dates_and_timezones_are_validated(activity, path, params):
    response = await activity.client.get(path, params=params)
    assert response.status_code == 422
    assert "Mars" not in response.text and "passwd" not in response.text


@pytest.mark.parametrize(
    "headers,expected",
    [
        ({}, 401),
        ({"Authorization": "Bearer utd_integration-token"}, 403),
        ({"Authorization": "Bearer utp_plugin-token"}, 403),
        ({"Authorization": "Bearer utpm_management-token"}, 403),
    ],
)
async def test_day_detail_keeps_application_authentication(headers, expected):
    """Delegated integration and plugin credentials keep their scoped APIs only."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(
            "/api/media-stats/activity/day", params={"date": "2026-01-01"}, headers=headers
        )
    assert response.status_code == expected


@pytest.mark.parametrize("zone", ZONES)
def test_day_bounds_cover_each_local_day_exactly(zone):
    tz = ZoneInfo(zone)
    day, lengths = date(2025, 1, 1), set()
    while day <= date(2026, 12, 31):
        start, end = day_bounds(day, tz)
        assert local_day(start, tz) == day and local_day(start - 1, tz) == day - timedelta(days=1)
        assert local_day(end - 1, tz) == day and local_day(end, tz) == day + timedelta(days=1)
        assert day_bounds(day + timedelta(days=1), tz)[0] == end
        lengths.add(end - start)
        day += timedelta(days=1)
    if zone == "Europe/London":
        assert lengths == {23 * 3600, 24 * 3600, 25 * 3600}
    if zone == "Australia/Lord_Howe":
        assert lengths == {int(23.5 * 3600), 24 * 3600, int(24.5 * 3600)}


def test_a_day_whose_midnight_is_skipped_starts_at_the_transition():
    tz = ZoneInfo("America/Santiago")
    skipped = [
        day
        for day in (date(2025, 1, 1) + timedelta(days=n) for n in range(730))
        if datetime.fromtimestamp(day_bounds(day, tz)[0], tz).hour == 1
    ]
    assert skipped  # clocks jump from 00:00 to 01:00 once a year
    for day in skipped:
        start, end = day_bounds(day, tz)
        assert end - start == 23 * 3600
        assert local_day(start - 1, tz) == day - timedelta(days=1)


def test_heatmap_window_is_whole_weeks_ending_today_in_the_zone():
    instant = int(datetime(2026, 10, 16, 10, 30, tzinfo=ZoneInfo("UTC")).timestamp())
    assert heatmap_window(ZoneInfo("UTC"), instant) == (date(2025, 10, 12), date(2026, 10, 16))
    # already the 17th at +14, still the 15th at -11
    assert heatmap_window(ZoneInfo("Pacific/Kiritimati"), instant)[1] == date(2026, 10, 17)
    assert heatmap_window(ZoneInfo("Pacific/Pago_Pago"), instant)[1] == date(2026, 10, 15)


def test_timezone_names_are_resolved_from_the_iana_database():
    assert resolve_timezone(None).key == "UTC"
    assert resolve_timezone("").key == "UTC"
    assert resolve_timezone("Asia/Kathmandu").key == "Asia/Kathmandu"
    # older browsers report backward-compatible names; the tzdata package keeps them
    assert resolve_timezone("Asia/Calcutta").key == "Asia/Calcutta"
    for name in ("Mars/Olympus_Mons", "../../etc/passwd", "/etc/localtime", "localtime", "America"):
        with pytest.raises(ActivityTimezoneError):
            resolve_timezone(name)
