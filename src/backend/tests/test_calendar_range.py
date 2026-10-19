"""#251: bounded calendar ranges and any week-start day."""

from datetime import date, datetime, timedelta, timezone
from uuid import UUID

import pytest

from src.database.models.movies import Movie
from src.database.models.tv_show import TVShow
from src.database.session import SessionLocal
from tests.test_game_files_flow import game_flow  # noqa: F401


def _unix(day: date, hour: int = 20) -> int:
    return int(datetime(day.year, day.month, day.day, hour, tzinfo=timezone.utc).timestamp())


@pytest.fixture(name="shows")
async def shows_fixture(flow):
    """One weekly show whose next episode is in 10 days, and a movie released in 300 days."""
    today = date.today()
    async with SessionLocal() as db:
        db.add(
            TVShow(
                id=UUID(int=1),
                user_id=flow.user_id,
                title="Weekly",
                sort_title="weekly",
                status="IN_PROGRESS",
                next_episode_air_at=_unix(today + timedelta(days=10)),
                next_episode_number=1,
                airing_interval_days=7,
            )
        )
        db.add(
            Movie(
                id=UUID(int=2),
                user_id=flow.user_id,
                title="Far away",
                sort_title="far away",
                status="WATCHLIST",
                release_date=today + timedelta(days=300),
            )
        )
        await db.commit()
    return today


async def entries(flow, first: date, last: date):
    response = await flow.client.get(
        "/api/calendar", params={"from_date": first.isoformat(), "to_date": last.isoformat()}
    )
    assert response.status_code == 200, response.text
    return response.json()


async def test_a_range_returns_only_that_range_and_marks_guesses_as_projected(flow, shows):
    today = shows
    # ten days ahead: the one confirmed episode
    near = await entries(flow, today + timedelta(days=8), today + timedelta(days=12))
    assert [(e["title"], e["next_episode_number"], e["is_projected"]) for e in near] == [
        ("Weekly", 1, False)
    ]
    # half a year ahead: only the usual weekly guess exists, and it says so
    later = await entries(flow, today + timedelta(days=150), today + timedelta(days=170))
    assert later and all(e["title"] == "Weekly" and e["is_projected"] is True for e in later)
    assert all(
        today + timedelta(days=150)
        <= datetime.fromtimestamp(e["air_at"], tz=timezone.utc).date()
        <= today + timedelta(days=171)
        for e in later
    )
    # a date-only release far ahead appears in its own month and nowhere else
    release = await entries(flow, today + timedelta(days=295), today + timedelta(days=305))
    assert [e["title"] for e in release if e["kind"] == "release"] == ["Far away"]
    quiet = await entries(flow, today + timedelta(days=60), today + timedelta(days=64))
    assert [e["title"] for e in quiet if e["kind"] == "release"] == []


async def test_ranges_are_bounded_and_validated(flow, shows):
    today = shows
    iso = lambda d: d.isoformat()  # noqa: E731
    for params in (
        {"from_date": iso(today)},  # one end only
        {"from_date": iso(today + timedelta(days=5)), "to_date": iso(today)},  # backwards
        {"from_date": iso(today), "to_date": iso(today + timedelta(days=100))},  # too long
        {
            "from_date": iso(today + timedelta(days=380)),
            "to_date": iso(today + timedelta(days=420)),
        },
        {
            "from_date": iso(today - timedelta(days=500)),
            "to_date": iso(today - timedelta(days=450)),
        },
    ):
        response = await flow.client.get("/api/calendar", params=params)
        assert response.status_code == 422, (params, response.text)
    # the old forward window still works exactly as before
    assert (await flow.client.get("/api/calendar", params={"days": 90})).status_code == 200


async def test_the_range_includes_both_ends_and_year_and_leap_boundaries(flow):
    async with SessionLocal() as db:
        for number, day in enumerate((date(2028, 2, 29), date(2028, 12, 31), date(2029, 1, 1)), 10):
            db.add(
                Movie(
                    id=UUID(int=number),
                    user_id=flow.user_id,
                    title=f"On {day}",
                    sort_title=f"on {day}",
                    status="WATCHLIST",
                    release_date=day,
                )
            )
        await db.commit()
    from src.api.routes import media_extras

    async with SessionLocal() as db:
        found = await media_extras.build_calendar_entries(
            db, flow.user_id, 14, window=(date(2028, 12, 31), date(2029, 1, 1))
        )
        assert sorted(e["title"] for e in found) == ["On 2028-12-31", "On 2029-01-01"]
        leap = await media_extras.build_calendar_entries(
            db, flow.user_id, 14, window=(date(2028, 2, 29), date(2028, 2, 29))
        )
        assert [e["title"] for e in leap] == ["On 2028-02-29"]


@pytest.mark.parametrize("start", range(7))
async def test_every_week_start_day_is_saved_per_user(flow, start):
    response = await flow.client.patch("/api/preferences", json={"calendar_week_start": start})
    assert response.status_code == 200, response.text
    assert (await flow.client.get("/api/preferences")).json()["calendar_week_start"] == start


@pytest.mark.parametrize("bad", [7, -1, "1", 1.5, True, None])
async def test_other_week_starts_are_refused(flow, bad):
    before = (await flow.client.get("/api/preferences")).json()["calendar_week_start"]
    response = await flow.client.patch("/api/preferences", json={"calendar_week_start": bad})
    assert response.status_code in (400, 422)
    assert (await flow.client.get("/api/preferences")).json()["calendar_week_start"] == before
