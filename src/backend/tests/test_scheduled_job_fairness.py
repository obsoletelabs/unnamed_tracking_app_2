"""Scheduled work selects due accounts fairly and records failures on its cadence."""

import asyncio
import time
from dataclasses import replace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import delete

from src.core.preferences import DEFAULTS, load_preferences, save_preferences, validate_preference
from src.database.models.job_setting import JobSetting
from src.database.models.user import User
from src.database.session import SessionLocal
from src.features import jobs
from src.features.metadata.anime.anilist import AniListError

HOUR = 60 * 60
BUILT_IN = ("airing_check", "plugin_updates", "anime_franchise_check")


@pytest.fixture
async def job_rows():
    """Built-in schedule rows are shared; start and finish each test without them."""
    async with SessionLocal() as db:
        await db.execute(delete(JobSetting).where(JobSetting.job_id.in_(BUILT_IN)))
        await db.commit()
    yield
    async with SessionLocal() as db:
        await db.execute(delete(JobSetting).where(JobSetting.job_id.in_(BUILT_IN)))
        await db.commit()


@pytest.fixture
async def importers():
    created = []

    async def make(*, active=True, **prefs):
        user_id = uuid4()
        async with SessionLocal() as db:
            db.add(
                User(
                    id=user_id,
                    username=f"importer_{user_id.hex}",
                    email=f"{user_id}@example.test",
                    password_hash="unused",
                    is_active=active,
                )
            )
            await db.commit()
            created.append(user_id)
            await save_preferences(
                db,
                user_id,
                {
                    "anilist_import_enabled": True,
                    "anilist_import_username": f"list{len(created)}",
                    **prefs,
                },
            )
        return user_id

    yield make
    async with SessionLocal() as db:
        await db.execute(delete(User).where(User.id.in_(created)))
        await db.commit()


async def preferences(user_id):
    async with SessionLocal() as db:
        return await load_preferences(db, user_id)


async def test_due_anilist_accounts_are_selected_before_the_batch_limit(importers, monkeypatch):
    now = int(time.time())
    # The first enabled accounts imported recently and are not due yet. Previously they
    # filled every slot of the four-account batch and later due accounts never ran.
    recent = [await importers(anilist_import_last_run_at=now - 60) for _ in range(5)]
    never = await importers()
    overdue = await importers(anilist_import_last_run_at=now - 2 * 24 * HOUR)
    blank = await importers(anilist_import_username="")
    disabled = await importers(active=False)
    imported = []

    async def fake_import(_db, user_id, username, update_existing):
        imported.append(user_id)
        assert username and update_existing is False
        return {"fetched": 0, "created": 0, "updated": 0, "skipped": 0, "errors": []}

    monkeypatch.setattr(jobs, "import_anilist_library", fake_import)
    await jobs._run_due_anilist_imports(now)

    assert {never, overdue} <= set(imported)
    assert not set(imported) & {*recent, blank, disabled}
    for user_id in (never, overdue):
        saved = await preferences(user_id)
        assert saved["anilist_import_last_run_at"] == now
        assert saved["anilist_import_last_attempt_at"] == now
        assert saved["anilist_import_last_error"] == ""


async def test_batches_rotate_by_oldest_attempt_across_ticks_and_restarts(importers, monkeypatch):
    now = int(time.time())
    accounts = [
        await importers(anilist_import_last_run_at=now - (10 - index) * 24 * HOUR)
        for index in range(6)
    ]
    imported = []

    async def fake_import(_db, user_id, *_args):
        imported.append(user_id)
        return {"fetched": 0}

    monkeypatch.setattr(jobs, "import_anilist_library", fake_import)
    await jobs._run_due_anilist_imports(now)
    mine = [user_id for user_id in imported if user_id in accounts]
    assert mine == accounts[: len(mine)] and len(mine) >= 1
    # A restart keeps nothing in memory; persisted attempt times hand the next tick to
    # the accounts that have waited longest.
    imported.clear()
    await jobs._run_due_anilist_imports(now + 60)
    await jobs._run_due_anilist_imports(now + 120)
    assert set(mine) | {user_id for user_id in imported if user_id in accounts} == set(accounts)
    assert not set(mine) & set(imported)


async def test_failed_anilist_import_is_reported_and_retried_on_its_cadence(importers, monkeypatch):
    now = int(time.time())
    owner = await importers(anilist_import_interval_minutes=60, anilist_import_last_run_at=1)
    failing = AsyncMock(
        side_effect=AniListError("Could not reach AniList: HTTPSConnectionPool(host='internal')")
    )
    monkeypatch.setattr(jobs, "import_anilist_library", failing)

    await jobs._run_due_anilist_imports(now)
    saved = await preferences(owner)
    assert saved["anilist_import_last_attempt_at"] == now
    assert saved["anilist_import_last_error"] == "Could not reach AniList."
    assert saved["anilist_import_last_run_at"] == 1

    # A persistent failure is not retried on every one-minute tick.
    for minute in range(1, 60):
        await jobs._run_due_anilist_imports(now + minute * 60)
    assert failing.await_count == 1

    monkeypatch.setattr(jobs, "import_anilist_library", AsyncMock(return_value={"fetched": 3}))
    await jobs._run_due_anilist_imports(now + HOUR)
    saved = await preferences(owner)
    assert saved["anilist_import_last_run_at"] == now + HOUR
    assert saved["anilist_import_last_error"] == ""


async def test_unexpected_import_failures_do_not_stop_the_batch_or_leak_details(
    importers, monkeypatch
):
    now = int(time.time())
    first = await importers()
    second = await importers()
    calls = []

    async def import_once(_db, user_id, *_args):
        calls.append(user_id)
        if user_id == first:
            raise RuntimeError("postgresql://secret@internal")
        return {"fetched": 1}

    monkeypatch.setattr(jobs, "import_anilist_library", import_once)
    await jobs._run_due_anilist_imports(now)
    assert {first, second} <= set(calls)
    failed = await preferences(first)
    assert failed["anilist_import_last_error"] == "The import could not finish."
    assert failed["anilist_import_last_attempt_at"] == now
    assert (await preferences(second))["anilist_import_last_run_at"] == now


async def test_disabling_before_a_queued_import_runs_skips_it(importers, monkeypatch):
    owner = await importers()
    async with SessionLocal() as db:
        await save_preferences(db, owner, {"anilist_import_enabled": False})
    failing = AsyncMock()
    monkeypatch.setattr(jobs, "import_anilist_library", failing)
    await jobs._run_due_anilist_import(owner, int(time.time()))
    failing.assert_not_awaited()
    assert (await preferences(owner))["anilist_import_last_attempt_at"] is None


@pytest.mark.parametrize(
    ("job_id", "patched"),
    [("airing_check", "check_airing_episodes"), ("plugin_updates", "_plugin_update_result")],
)
async def test_failed_builtin_runs_are_recorded_and_wait_for_their_interval(
    job_rows, monkeypatch, job_id, patched
):
    monkeypatch.setattr(jobs, patched, AsyncMock(side_effect=RuntimeError("provider down")))
    spec = jobs.JOBS[job_id]
    tasks = asyncio.all_tasks()
    spec.start("needed")
    await asyncio.gather(*(asyncio.all_tasks() - tasks))
    assert not spec.is_running()
    async with SessionLocal() as db:
        row = await db.get(JobSetting, job_id)
        assert row.last_run_at is not None and row.last_result["completed"] is False
        summary = (await jobs.describe(db, spec))["last_summary"]
        assert "retry at the next scheduled time" in summary
        assert "provider down" not in summary
        later = row.last_run_at
        assert await jobs.due_jobs(db, [spec], later + 60) == []
        assert await jobs.due_jobs(db, [spec], later + row.interval_minutes * 60) == [spec]
        await db.commit()


async def test_unfinished_franchise_batches_continue_sooner_than_plugin_server_tasks(job_rows):
    franchise = jobs.JOBS["anime_franchise_check"]
    plugin_task = replace(franchise, id=f"plugin:{uuid4()}", plugin_id="example.tasks")
    now = int(time.time())
    try:
        async with SessionLocal() as db:
            for spec in (franchise, plugin_task):
                row = await jobs.get_setting(db, spec)
                row.enabled, row.last_run_at = True, now
                row.last_result = {"completed": False, "continue_after_seconds": 300}
            await db.commit()
            assert await jobs.due_jobs(db, [franchise, plugin_task], now + 240) == []
            assert await jobs.due_jobs(db, [franchise, plugin_task], now + 300) == [franchise]
            # A completed pass, or a failure without a continuation, uses the interval.
            for result in ({"completed": True}, {"completed": False, "error": "failed"}):
                row = await db.get(JobSetting, franchise.id)
                row.last_result = result
                await db.commit()
                assert await jobs.due_jobs(db, [franchise], now + 300) == []
                assert await jobs.due_jobs(db, [franchise], now + 24 * HOUR) == [franchise]
            row.enabled = False
            row.last_result = {"completed": False, "continue_after_seconds": 300}
            await db.commit()
            assert await jobs.due_jobs(db, [franchise], now + 24 * HOUR) == []
    finally:
        async with SessionLocal() as db:
            await db.execute(delete(JobSetting).where(JobSetting.job_id == plugin_task.id))
            await db.commit()


async def test_crashed_franchise_check_keeps_its_cursor(job_rows, monkeypatch):
    cursor = {
        "completed": False,
        "cursor_user": str(uuid4()),
        "cursor_franchise": 42,
        "stalled": 1,
        "continue_after_seconds": 600,
    }
    async with SessionLocal() as db:
        row = await jobs.get_setting(db, jobs.JOBS["anime_franchise_check"])
        row.last_result, row.last_run_at = cursor, 1
        await db.commit()
    seen = []

    async def crash(previous):
        seen.append(previous)
        raise RuntimeError("database went away")

    monkeypatch.setattr(jobs, "run_franchise_check", crash)
    tasks = asyncio.all_tasks()
    jobs.JOBS["anime_franchise_check"].start("needed")
    await asyncio.gather(*(asyncio.all_tasks() - tasks))
    assert seen == [cursor]
    async with SessionLocal() as db:
        saved = (await db.get(JobSetting, "anime_franchise_check")).last_result
    assert {key: saved[key] for key in ("cursor_user", "cursor_franchise", "stalled")} == {
        key: cursor[key] for key in ("cursor_user", "cursor_franchise", "stalled")
    }
    assert saved["completed"] is False and "continue_after_seconds" not in saved
    assert "resume at the next scheduled time" in saved["error"]


async def test_unreachable_airing_providers_are_reported_not_counted_as_checked(monkeypatch):
    from src.database.models.anime import Anime, AnimeEpisode, AnimeSeason, AnimeStatus
    from src.features.metadata import refresh

    owner = uuid4()
    async with SessionLocal() as db:
        db.add(
            User(
                id=owner,
                username=f"airing_{owner.hex}",
                email=f"{owner}@example.test",
                password_hash="unused",
            )
        )
        show = Anime(
            user_id=owner,
            title="Airing show",
            sort_title="airing show",
            status=AnimeStatus.IN_PROGRESS,
            anilist_id="777",
            is_airing=True,
            studios=[],
            countries=[],
            languages=[],
            genres=[],
            tags=[],
            features=[],
        )
        db.add(show)
        await db.flush()
        season = AnimeSeason(show_id=show.id, season_number=1)
        db.add(season)
        await db.flush()
        db.add(AnimeEpisode(season_id=season.id, episode_number=1))
        await db.commit()
    unavailable = AsyncMock(return_value=(None, False, None, None, ["AniList unavailable"]))
    monkeypatch.setattr(refresh, "fetch_airing_status", unavailable)
    monkeypatch.setattr(refresh, "fetch_is_airing", AsyncMock(return_value=(None, ["down"])))
    try:
        result = await refresh.check_airing_episodes(force=True)
        assert result["failed"] >= 1 and result["checked"] == 0
        assert "unavailable; retried next run" in jobs._summarize_airing(result)
        # The cheap check used by title pages keeps returning quietly.
        async with SessionLocal() as db:
            stored = await db.get(Anime, show.id)
            assert await refresh.quick_check_anime_season(stored, stored.seasons[0]) == 0
    finally:
        async with SessionLocal() as db:
            await db.execute(delete(User).where(User.id == owner))
            await db.commit()


def test_import_report_preferences_are_bounded_and_default_empty():
    assert DEFAULTS["anilist_import_last_attempt_at"] is None
    assert DEFAULTS["anilist_import_last_error"] == ""
    for key, value in (
        ("anilist_import_last_attempt_at", -1),
        ("anilist_import_last_attempt_at", True),
        ("anilist_import_last_error", "x" * 301),
        ("anilist_import_last_error", None),
        ("notify_franchise_season", "yes"),
    ):
        with pytest.raises(ValueError):
            validate_preference(key, value)
    assert validate_preference("anilist_import_last_attempt_at", 5) == 5
