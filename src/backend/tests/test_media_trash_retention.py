"""Emptying the movie, TV show and anime trash, and the opt-in retention
clean-up that runs in the existing trash sweep (features/trash/media_titles.py)."""

import asyncio
import time
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.sql.functions import count as sql_count

from src.core import preferences
from src.database.models.anime import Anime, AnimeEpisode, AnimeSeason
from src.database.models.movies import Movie
from src.database.models.tv_show import TVEpisode, TVSeason, TVShow
from src.database.models.user import User
from src.database.models.user_preferences import UserPreferences
from src.database.session import SessionLocal
from src.features.trash import media_titles, sweep
from tests.test_game_files_flow import game_flow  # noqa: F401  (registers the "flow" fixture)

DAY = 24 * 60 * 60
KINDS = {
    "movie": SimpleNamespace(model=Movie, table="movies", season=None, episode=None),
    "tv": SimpleNamespace(model=TVShow, table="tv_shows", season=TVSeason, episode=TVEpisode),
    "anime": SimpleNamespace(model=Anime, table="anime", season=AnimeSeason, episode=AnimeEpisode),
}


async def _title(kind: str, user_id, title: str, deleted_at: int | None) -> uuid.UUID:
    """A title as stored, so deletion times can be exact; shows get a season and episode."""
    spec = KINDS[kind]
    async with SessionLocal() as db:
        row = spec.model(user_id=user_id, title=title, sort_title=title, deleted_at=deleted_at)
        db.add(row)
        await db.flush()
        if spec.season is not None:
            season = spec.season(show_id=row.id, season_number=1, episode_count=1)
            db.add(season)
            await db.flush()
            db.add(spec.episode(season_id=season.id, episode_number=1))
        await db.commit()
        return row.id


async def _titles(kind: str, user_id) -> dict[str, int | None]:
    model = KINDS[kind].model
    async with SessionLocal() as db:
        rows = await db.execute(
            select(model.title, model.deleted_at).where(model.user_id == user_id)
        )
        return dict(rows.tuples().all())


async def _children(kind: str, show_id: uuid.UUID) -> tuple[int, int]:
    spec = KINDS[kind]
    if spec.season is None:
        return (0, 0)
    async with SessionLocal() as db:
        seasons = await db.scalar(
            select(sql_count()).select_from(spec.season).where(spec.season.show_id == show_id)
        )
        episodes = await db.scalar(
            select(sql_count())
            .select_from(spec.episode)
            .join(spec.season, spec.season.id == spec.episode.season_id)
            .where(spec.season.show_id == show_id)
        )
        return (int(seasons or 0), int(episodes or 0))


@asynccontextmanager
async def _another_user():
    async with SessionLocal() as db:
        user = User(
            username=f"o_{uuid.uuid4().hex[:10]}",
            email=f"{uuid.uuid4().hex[:10]}@example.test",
            password_hash="x",
        )
        db.add(user)
        await db.commit()
        user_id = user.id
    try:
        yield user_id
    finally:
        async with SessionLocal() as db:
            await db.execute(delete(User).where(User.id == user_id))
            await db.commit()


@asynccontextmanager
async def _undeletable(kind: str, title: str):
    """A real database failure while deleting one title (after its children)."""
    name = f"refuse_{uuid.uuid4().hex[:12]}"
    table = KINDS[kind].table
    async with SessionLocal() as db:
        await db.execute(
            text(
                f"CREATE FUNCTION {name}() RETURNS trigger AS $$ BEGIN "
                f"IF OLD.title = '{title}' THEN RAISE EXCEPTION 'refused'; END IF; "
                "RETURN OLD; END $$ LANGUAGE plpgsql"
            )
        )
        await db.execute(
            text(
                f"CREATE TRIGGER {name} BEFORE DELETE ON {table} "
                f"FOR EACH ROW EXECUTE FUNCTION {name}()"
            )
        )
        await db.commit()
    try:
        yield
    finally:
        async with SessionLocal() as db:
            await db.execute(text(f"DROP TRIGGER IF EXISTS {name} ON {table}"))
            await db.execute(text(f"DROP FUNCTION IF EXISTS {name}()"))
            await db.commit()


async def _until_waiting_for_a_lock(holder) -> None:
    """Wait until another connection is blocked on a lock `holder` has taken."""
    holder_pid = await holder.scalar(text("SELECT pg_backend_pid()"))
    for _ in range(200):
        async with SessionLocal() as db:
            waiting = await db.scalar(
                text(
                    "SELECT count(*) FROM pg_stat_activity "
                    "WHERE :holder = ANY(pg_blocking_pids(pid))"
                ),
                {"holder": holder_pid},
            )
        if waiting:
            return
        await asyncio.sleep(0.05)
    raise AssertionError("the other request never waited for the lock")


async def _set_retention(user_id, value) -> None:
    async with SessionLocal() as db:
        row = await db.scalar(select(UserPreferences).where(UserPreferences.user_id == user_id))
        if row is None:
            db.add(UserPreferences(user_id=user_id, data={"media_trash_retention_days": value}))
        else:
            row.data = {**row.data, "media_trash_retention_days": value}
        await db.commit()


# ---------------------------------------------------------------------------
# Empty trash
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", list(KINDS))
async def test_empty_trash_requires_explicit_confirmation(flow, kind: str) -> None:
    deleted_at = int(time.time())
    await _title(kind, flow.user_id, "Trashed", deleted_at)
    for body in (None, {}, {"confirm": False}, {"confirm": "yes"}, {"confirm": 1}):
        response = await flow.client.post(f"/api/{kind}/trash/empty", json=body)
        assert response.status_code == 422, (body, response.text)
    response = await flow.client.post(
        f"/api/{kind}/trash/empty", json={"confirm": True, "deleted_through": -1}
    )
    assert response.status_code == 422
    assert await _titles(kind, flow.user_id) == {"Trashed": deleted_at}


@pytest.mark.parametrize("kind", list(KINDS))
async def test_empty_trash_removes_only_this_kind_of_the_owners_trash(flow, kind: str) -> None:
    now = int(time.time())
    active = await _title(kind, flow.user_id, "Active", None)
    trashed = [await _title(kind, flow.user_id, f"Trashed {n}", now - n) for n in (1, 2)]
    other_kind = next(other for other in KINDS if other != kind)
    await _title(other_kind, flow.user_id, "Other kind", now)
    async with _another_user() as stranger:
        await _title(kind, stranger, "Stranger trashed", now)
        await _title(kind, stranger, "Stranger active", None)

        response = await flow.client.post(f"/api/{kind}/trash/empty", json={"confirm": True})
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["deleted"] == 2
        assert result["failed"] == 0
        assert result["failed_items"] == []
        assert result["remaining"] == 0
        assert result["deleted_through"] >= now

        assert await _titles(kind, flow.user_id) == {"Active": None}
        assert await _titles(kind, stranger) == {"Stranger trashed": now, "Stranger active": None}
    assert await _titles(other_kind, flow.user_id) == {"Other kind": now}
    assert (await flow.client.get(f"/api/{kind}/trash")).json() == []
    # children go with their title, and an active title keeps its own
    for row_id in trashed:
        assert await _children(kind, row_id) == (0, 0)
    assert await _children(kind, active) == ((1, 1) if KINDS[kind].season else (0, 0))


@pytest.mark.parametrize("kind", list(KINDS))
async def test_empty_trash_keeps_titles_trashed_after_confirmation(flow, kind: str) -> None:
    seen = int(time.time()) - 100
    await _title(kind, flow.user_id, "Seen", seen)
    await _title(kind, flow.user_id, "Older", seen - DAY)
    await _title(kind, flow.user_id, "Trashed later", seen + 1)

    response = await flow.client.post(
        f"/api/{kind}/trash/empty", json={"confirm": True, "deleted_through": seen}
    )
    assert response.status_code == 200, response.text
    assert response.json() == {
        "deleted": 2,
        "failed": 0,
        "failed_items": [],
        "remaining": 0,
        "deleted_through": seen,
    }
    assert await _titles(kind, flow.user_id) == {"Trashed later": seen + 1}


async def test_empty_trash_works_in_bounded_batches(flow, monkeypatch) -> None:
    monkeypatch.setattr(media_titles, "EMPTY_BATCH_SIZE", 2)
    now = int(time.time())
    for n in range(5):
        await _title("tv", flow.user_id, f"Show {n}", now - n)

    deleted, remaining = [], []
    for _ in range(3):
        result = (
            await flow.client.post(
                "/api/tv/trash/empty", json={"confirm": True, "deleted_through": now}
            )
        ).json()
        deleted.append(result["deleted"])
        remaining.append(result["remaining"])
    assert deleted == [2, 2, 1]
    assert remaining == [3, 1, 0]
    assert await _titles("tv", flow.user_id) == {}


@pytest.mark.parametrize("kind", list(KINDS))
async def test_empty_trash_reports_a_failed_title_and_keeps_it_whole(flow, kind: str) -> None:
    now = int(time.time())
    title = f"Refused {uuid.uuid4().hex[:8]}"
    refused = await _title(kind, flow.user_id, title, now - 10)
    await _title(kind, flow.user_id, "Deletable", now - 5)
    async with _undeletable(kind, title):
        response = await flow.client.post(f"/api/{kind}/trash/empty", json={"confirm": True})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["deleted"] == 1
    assert result["failed"] == 1
    assert result["failed_items"] == [{"id": str(refused), "title": title, "deleted_at": now - 10}]
    assert result["remaining"] == 1
    # the failed title is rolled back as a whole: still in the trash, children intact
    assert await _titles(kind, flow.user_id) == {title: now - 10}
    assert await _children(kind, refused) == ((1, 1) if KINDS[kind].season else (0, 0))


async def test_empty_trash_never_deletes_a_title_restored_meanwhile(flow) -> None:
    now = int(time.time())
    restored = await _title("movie", flow.user_id, "Restored", now - 10)
    await _title("movie", flow.user_id, "Gone", now - 5)
    async with SessionLocal() as restore:
        row = await restore.get(Movie, restored, with_for_update=True)
        row.deleted_at = None
        await restore.flush()
        emptying = asyncio.create_task(
            flow.client.post("/api/movie/trash/empty", json={"confirm": True})
        )
        await _until_waiting_for_a_lock(restore)
        assert not emptying.done()
        await restore.commit()
    result = (await emptying).json()
    assert result["deleted"] == 1
    assert result["remaining"] == 0
    assert await _titles("movie", flow.user_id) == {"Restored": None}


# ---------------------------------------------------------------------------
# Retention preference
# ---------------------------------------------------------------------------


async def test_retention_defaults_to_never_for_new_and_existing_accounts(flow) -> None:
    assert preferences.DEFAULTS["media_trash_retention_days"] == 0
    assert (await flow.client.get("/api/preferences")).json()["media_trash_retention_days"] == 0
    # an existing account with saved preferences, but no retention choice
    async with SessionLocal() as db:
        db.add(UserPreferences(user_id=flow.user_id, data={"ui_theme": "dark"}))
        await db.commit()
    assert (await flow.client.get("/api/preferences")).json()["media_trash_retention_days"] == 0
    for kind in KINDS:
        await _title(kind, flow.user_id, "Ancient", int(time.time()) - 400 * DAY)

    await media_titles.purge_expired_media_titles()
    for kind in KINDS:
        assert list(await _titles(kind, flow.user_id)) == ["Ancient"]


async def test_retention_accepts_only_the_offered_periods(flow) -> None:
    for days in (7, 14, 30, 90, 0):
        response = await flow.client.patch(
            "/api/preferences", json={"media_trash_retention_days": days}
        )
        assert response.status_code == 200, response.text
        assert response.json()["media_trash_retention_days"] == days
    for value in (1, 365, -7, True, False, "30", 30.0, None, [30]):
        response = await flow.client.patch(
            "/api/preferences", json={"media_trash_retention_days": value}
        )
        assert response.status_code == 422, (value, response.text)
    assert (await flow.client.get("/api/preferences")).json()["media_trash_retention_days"] == 0


# ---------------------------------------------------------------------------
# Scheduled retention
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("days", [7, 14, 30, 90])
async def test_retention_purges_titles_older_than_the_chosen_period(flow, days: int) -> None:
    now = int(time.time())
    cutoff = now - days * DAY
    await _set_retention(flow.user_id, days)
    expired = {}
    for kind in KINDS:
        expired[kind] = await _title(kind, flow.user_id, "Expired", cutoff)
        await _title(kind, flow.user_id, "Recent", cutoff + 60)
        await _title(kind, flow.user_id, "Active", None)
    async with _another_user() as stranger, _another_user() as shorter:
        await _title("movie", stranger, "Stranger old", now - 400 * DAY)
        await _set_retention(shorter, 7)
        await _title("movie", shorter, "Shorter old", now - 8 * DAY)
        await _title("movie", shorter, "Shorter recent", now - 6 * DAY)

        counts = await media_titles.purge_expired_media_titles(now)

        assert await _titles("movie", stranger) == {"Stranger old": now - 400 * DAY}
        assert await _titles("movie", shorter) == {"Shorter recent": now - 6 * DAY}
    assert counts["purged_titles"] >= 4
    assert counts["failed_titles"] == 0
    for kind, row_id in expired.items():
        assert await _titles(kind, flow.user_id) == {"Recent": cutoff + 60, "Active": None}
        assert await _children(kind, row_id) == (0, 0)


async def test_retention_never_and_unrecognised_values_purge_nothing(flow) -> None:
    old = int(time.time()) - 400 * DAY
    await _title("anime", flow.user_id, "Old", old)
    for stored in (0, "30", 30.5, True, 365, -1, None, {"days": 30}):
        await _set_retention(flow.user_id, stored)
        await media_titles.purge_expired_media_titles()
        assert await _titles("anime", flow.user_id) == {"Old": old}, stored


async def test_retention_rechecks_the_choice_inside_its_transaction(flow, monkeypatch) -> None:
    """Choosing Never after the sweep listed the account still prevents deletion."""
    old = int(time.time()) - 40 * DAY
    await _title("movie", flow.user_id, "Old", old)
    await _set_retention(flow.user_id, 30)
    real_purge = media_titles._purge_account  # pylint: disable=protected-access

    async def changed_meanwhile(user_id, now, budget):
        async with SessionLocal() as db:
            await preferences.save_preferences(db, user_id, {"media_trash_retention_days": 0})
        return await real_purge(user_id, now, budget)

    monkeypatch.setattr(media_titles, "_purge_account", changed_meanwhile)
    await media_titles.purge_expired_media_titles()
    assert await _titles("movie", flow.user_id) == {"Old": old}


async def test_retention_work_is_bounded_per_kind_and_per_sweep(flow, monkeypatch) -> None:
    monkeypatch.setattr(media_titles, "RETENTION_BATCH_SIZE", 2)
    monkeypatch.setattr(media_titles, "RETENTION_SWEEP_LIMIT", 3)
    now = int(time.time())
    await _set_retention(flow.user_id, 7)
    for kind in ("movie", "tv"):
        for n in range(3):
            await _title(kind, flow.user_id, f"{kind} {n}", now - 10 * DAY - n)

    first = await media_titles.purge_expired_media_titles(now)
    assert first["purged_titles"] == 3
    # oldest first: two movies, then one show; the rest waits for the next sweep
    assert await _titles("movie", flow.user_id) == {"movie 0": now - 10 * DAY}
    assert len(await _titles("tv", flow.user_id)) == 2

    await media_titles.purge_expired_media_titles(now)
    await media_titles.purge_expired_media_titles(now)
    assert await _titles("movie", flow.user_id) == {}
    assert await _titles("tv", flow.user_id) == {}


async def test_retention_failure_keeps_the_title_and_continues(flow) -> None:
    now = int(time.time())
    title = f"Refused {uuid.uuid4().hex[:8]}"
    await _set_retention(flow.user_id, 7)
    refused = await _title("tv", flow.user_id, title, now - 20 * DAY)
    await _title("tv", flow.user_id, "Expired", now - 20 * DAY)
    await _title("movie", flow.user_id, "Expired movie", now - 20 * DAY)
    async with _undeletable("tv", title):
        counts = await media_titles.purge_expired_media_titles(now)
    assert counts["failed_titles"] == 1
    assert counts["purged_titles"] >= 2
    assert await _titles("tv", flow.user_id) == {title: now - 20 * DAY}
    assert await _children("tv", refused) == (1, 1)
    assert await _titles("movie", flow.user_id) == {}


async def test_retention_runs_in_the_existing_trash_sweep(monkeypatch) -> None:
    calls: list[str] = []

    async def game_files() -> dict[str, int]:
        calls.append("game files")
        return {}

    async def titles() -> dict[str, int]:
        calls.append("titles")
        raise RuntimeError("database unavailable")

    async def sessions(_db) -> int:
        calls.append("sessions")
        return 0

    async def stop(_seconds) -> None:
        raise asyncio.CancelledError

    monkeypatch.setattr(sweep, "purge_expired_trash", game_files)
    monkeypatch.setattr(sweep, "purge_expired_media_titles", titles)
    monkeypatch.setattr(sweep, "purge_expired_sessions", sessions)
    monkeypatch.setattr(sweep.asyncio, "sleep", stop)
    with pytest.raises(asyncio.CancelledError):
        await sweep.run_sweep_loop()
    # one loop, one interval: a failed title clean-up does not stop the rest
    assert calls == ["game files", "titles", "sessions"]
