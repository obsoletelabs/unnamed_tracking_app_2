"""Scheduled work selects due accounts fairly and records failures on its cadence."""

import asyncio
import time
from dataclasses import replace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import delete

from src.core.preferences import DEFAULTS, save_preferences, validate_preference
from src.database.models.job_setting import JobSetting
from src.database.models.user import User
from src.database.session import SessionLocal
from src.features import anilist_jobs, jobs
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
async def importers(monkeypatch):
    """Accounts with a saved AniList username and an enabled personal import schedule."""
    created = []
    monkeypatch.setattr(anilist_jobs, "_running", {})

    async def make(*, active=True, enabled=True, username=None, interval=24 * 60, last_run=None):
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
            name = f"list{len(created)}" if username is None else username
            await save_preferences(db, user_id, {"anilist_import_username": name})
            db.add(
                JobSetting(
                    job_id=anilist_jobs.anilist_job_id(user_id),
                    user_id=user_id,
                    enabled=enabled,
                    interval_minutes=interval,
                    last_run_at=last_run,
                )
            )
            await db.commit()
        return user_id

    yield make
    tasks = list(anilist_jobs._running.values())
    await asyncio.gather(*tasks, return_exceptions=True)
    async with SessionLocal() as db:
        await db.execute(delete(User).where(User.id.in_(created)))
        await db.commit()


async def due_imports(now, among):
    """The AniList schedules of `among` that the job loop would start at `now`, in order."""
    async with SessionLocal() as db:
        specs = [
            spec
            for spec in await jobs.get_scheduled_user_jobs(db)
            if spec.user_id in among and spec.id.startswith("user-anilist:")
        ]
        due = await jobs.due_jobs(db, specs, now)
        await db.commit()
    return [spec.user_id for spec in due]


async def import_row(user_id):
    async with SessionLocal() as db:
        return await db.get(JobSetting, anilist_jobs.anilist_job_id(user_id))


async def run_imports(user_ids):
    async with SessionLocal() as db:
        specs = [(await anilist_jobs.get_anilist_jobs(db, user_id))[0] for user_id in user_ids]
    for spec in specs:
        spec.start("needed")
    await asyncio.gather(*anilist_jobs._running.values())
    return specs


async def test_due_anilist_accounts_are_selected_before_the_batch_limit(importers):
    now = int(time.time())
    # Accounts that imported recently are not due. They must never take the place of later
    # due accounts, however many of them are enabled.
    recent = [await importers(last_run=now - 60) for _ in range(5)]
    never = await importers()
    overdue = await importers(last_run=now - 2 * 24 * HOUR)
    blank = await importers(username="")
    inactive = await importers(active=False)
    disabled = await importers(enabled=False, last_run=1)
    among = {*recent, never, overdue, blank, inactive, disabled}

    assert await due_imports(now, among) == [never, overdue]


async def test_due_imports_rotate_by_oldest_run_across_ticks_and_restarts(importers, monkeypatch):
    now = int(time.time())
    accounts = [await importers(last_run=now - (10 - index) * 24 * HOUR) for index in range(6)]
    # Waiting longest goes first, so the shared import slots serve the oldest schedules.
    assert await due_imports(now, set(accounts)) == accounts
    imported = []

    async def fake_import(_db, user_id, *_args):
        imported.append(user_id)
        return {"fetched": 0, "created": 0, "updated": 0, "skipped": 0}

    monkeypatch.setattr(anilist_jobs, "import_anilist_library", fake_import)
    await run_imports(accounts[:2])
    # Nothing is kept in memory: persisted run times hand the next tick to the others.
    assert await due_imports(now + 60, set(accounts)) == accounts[2:]
    assert sorted(imported) == sorted(accounts[:2])


async def test_failed_anilist_import_is_reported_and_retried_on_its_cadence(
    importers, monkeypatch
):
    owner = await importers(interval=60, last_run=1)
    failing = AsyncMock(
        side_effect=AniListError("Could not reach AniList: HTTPSConnectionPool(host='internal')")
    )
    monkeypatch.setattr(anilist_jobs, "import_anilist_library", failing)
    (spec,) = await run_imports([owner])

    row = await import_row(owner)
    assert row.last_run_at > 1 and row.last_result["completed"] is False
    summary = spec.summarize(row.last_result)
    assert "AniList" in summary and "internal" not in summary
    # A persistent failure is not retried on every one-minute tick, only on its interval.
    assert await due_imports(row.last_run_at + 59 * 60, {owner}) == []
    assert await due_imports(row.last_run_at + HOUR, {owner}) == [owner]

    monkeypatch.setattr(
        anilist_jobs,
        "import_anilist_library",
        AsyncMock(return_value={"fetched": 3, "created": 0, "updated": 0, "skipped": 3}),
    )
    await run_imports([owner])
    assert (await import_row(owner)).last_result["completed"] is True


async def test_unexpected_import_failures_do_not_stop_others_or_leak_details(
    importers, monkeypatch
):
    first = await importers()
    second = await importers()

    async def import_once(_db, user_id, *_args):
        if user_id == first:
            raise RuntimeError("postgresql://secret@internal")
        return {"fetched": 1, "created": 1, "updated": 0, "skipped": 0}

    monkeypatch.setattr(anilist_jobs, "import_anilist_library", import_once)
    specs = await run_imports([first, second])
    failed = await import_row(first)
    assert failed.last_run_at and failed.last_result["completed"] is False
    assert "secret" not in specs[0].summarize(failed.last_result)
    assert (await import_row(second)).last_result["completed"] is True


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


def test_franchise_preferences_are_validated():
    assert DEFAULTS["notify_franchise_season"] is False
    assert DEFAULTS["notify_franchise_extra"] is False
    with pytest.raises(ValueError):
        validate_preference("notify_franchise_season", "yes")
