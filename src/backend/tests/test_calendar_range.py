"""Bounded calendar ranges and all seven week-start days."""

import time
import uuid
from datetime import date, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import delete

from src.api.routes.media_extras import (
    CALENDAR_RANGE_MAX_AHEAD_DAYS,
    CALENDAR_RANGE_MAX_DAYS,
    build_calendar_entries,
    get_calendar,
)
from src.core.preferences import DEFAULTS, load_preferences, save_preferences, validate_preference
from src.database.models.anime import Anime, AnimeSeason, AnimeStatus
from src.database.models.movies import Movie, MovieStatus
from src.database.models.user import User
from src.database.session import SessionLocal


@pytest.mark.parametrize("day", range(7))
def test_every_week_start_day_is_valid(day):
    assert validate_preference("calendar_week_start", day) == day


@pytest.mark.parametrize("bad", [-1, 7, "1", None, 1.5])
def test_other_week_start_values_are_rejected(bad):
    with pytest.raises(ValueError):
        validate_preference("calendar_week_start", bad)


def test_week_start_default_stays_sunday():
    assert DEFAULTS["calendar_week_start"] == 0


async def _make_user(db):
    user = User(
        username=f"r_{uuid.uuid4().hex[:10]}",
        email=f"{uuid.uuid4().hex[:10]}@example.test",
        password_hash="x",
    )
    db.add(user)
    await db.flush()
    return user


def _movie(user_id, title, when):
    return Movie(
        user_id=user_id,
        title=title,
        sort_title=title.lower(),
        status=MovieStatus.WISHLIST,
        release_date=when,
    )


@pytest.mark.asyncio
async def test_week_start_persists_per_user():
    async with SessionLocal() as db:
        first = await _make_user(db)
        second = await _make_user(db)
        first_id, second_id = first.id, second.id
        try:
            await save_preferences(db, first_id, {"calendar_week_start": 4})
            await save_preferences(db, second_id, {"calendar_week_start": 6})
            await db.commit()
            assert (await load_preferences(db, first_id))["calendar_week_start"] == 4
            assert (await load_preferences(db, second_id))["calendar_week_start"] == 6
        finally:
            await db.rollback()
            await db.execute(delete(User).where(User.id.in_([first_id, second_id])))
            await db.commit()


@pytest.mark.asyncio
async def test_range_returns_only_that_window_across_year_and_leap_day():
    async with SessionLocal() as db:
        user = await _make_user(db)
        user_id = user.id
        today = date.today()
        far = today + timedelta(days=400)
        try:
            db.add_all(
                [
                    _movie(user_id, "On the start", far),
                    _movie(user_id, "On the end", far + timedelta(days=30)),
                    _movie(user_id, "Just before", far - timedelta(days=1)),
                    _movie(user_id, "Just after", far + timedelta(days=31)),
                ]
            )
            await db.commit()
            entries = await build_calendar_entries(
                db, user_id, 90, start=far, end=far + timedelta(days=30)
            )
            assert {e["title"] for e in entries} == {"On the start", "On the end"}
            # the default rolling window stays as it was
            assert await build_calendar_entries(db, user_id, 90) == []
        finally:
            await db.rollback()
            await db.execute(delete(User).where(User.id == user_id))
            await db.commit()


@pytest.mark.asyncio
async def test_range_includes_leap_day_and_year_boundaries():
    async with SessionLocal() as db:
        user = await _make_user(db)
        user_id = user.id
        try:
            db.add_all(
                [
                    _movie(user_id, "Leap", date(2028, 2, 29)),
                    _movie(user_id, "New Year Eve", date(2027, 12, 31)),
                    _movie(user_id, "New Year", date(2028, 1, 1)),
                ]
            )
            await db.commit()
            window = await build_calendar_entries(
                db, user_id, 90, start=date(2027, 12, 31), end=date(2028, 3, 1)
            )
            assert {e["title"] for e in window} == {"Leap", "New Year Eve", "New Year"}
            narrow = await build_calendar_entries(
                db, user_id, 90, start=date(2028, 2, 28), end=date(2028, 3, 1)
            )
            assert [e["title"] for e in narrow] == ["Leap"]
        finally:
            await db.rollback()
            await db.execute(delete(User).where(User.id == user_id))
            await db.commit()


@pytest.mark.asyncio
async def test_range_projects_estimates_beyond_the_default_window_and_flags_them():
    async with SessionLocal() as db:
        user = await _make_user(db)
        user_id = user.id
        try:
            show = Anime(
                user_id=user_id,
                title="Weekly",
                sort_title="weekly",
                status=AnimeStatus.IN_PROGRESS,
                studios=[],
                countries=[],
                languages=[],
                genres=[],
                tags=[],
                features=[],
                locked_fields=[],
                next_episode_number=2,
                next_episode_air_at=int(time.time()) + 3 * 86400,
            )
            db.add(show)
            await db.flush()
            db.add(AnimeSeason(show_id=show.id, season_number=1, episode_count=40))
            await db.commit()
            start = date.today() + timedelta(days=100)
            entries = await build_calendar_entries(
                db, user_id, 90, start=start, end=start + timedelta(days=20)
            )
            assert entries, "estimates reach into a later range"
            assert all(e["is_projected"] for e in entries)
            assert all(e["next_episode_number"] > 2 for e in entries)
        finally:
            await db.rollback()
            await db.execute(delete(User).where(User.id == user_id))
            await db.commit()


@pytest.mark.asyncio
async def test_range_is_scoped_to_the_caller():
    async with SessionLocal() as db:
        mine = await _make_user(db)
        other = await _make_user(db)
        mine_id, other_id = mine.id, other.id
        when = date.today() + timedelta(days=200)
        try:
            db.add(_movie(other_id, "Someone else", when))
            await db.commit()
            assert not await build_calendar_entries(db, mine_id, 90, start=when, end=when)
        finally:
            await db.rollback()
            await db.execute(delete(User).where(User.id.in_([mine_id, other_id])))
            await db.commit()


@pytest.mark.asyncio
async def test_range_route_bounds():
    today = date.today()
    me = SimpleNamespace(id=uuid.uuid4())

    async def call(start, end):
        return await get_calendar(14, start, end, None, me)  # type: ignore[arg-type]

    for start, end in (
        (today, None),
        (None, today),
        (today, today - timedelta(days=1)),
        (today, today + timedelta(days=CALENDAR_RANGE_MAX_DAYS)),
        (today, today + timedelta(days=CALENDAR_RANGE_MAX_AHEAD_DAYS + 1)),
        (today - timedelta(days=4000), today - timedelta(days=3990)),
    ):
        with pytest.raises(HTTPException) as caught:
            await call(start, end)
        assert caught.value.status_code == 422
