"""Backups and the trash sweep are scheduled by the central job registry."""

import asyncio
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.api.routes import jobs as job_routes
from src.api.routes.export_import import backup_status
from src.database.models.job_setting import JobSetting
from src.database.models.user import User
from src.database.session import SessionLocal
from src.features import jobs
from src.features.trash import sweep


@pytest.fixture(autouse=True)
async def _clean_settings():
    yield
    async with SessionLocal() as db:
        for job_id in ("library_backups", "trash_sweep"):
            row = await db.get(JobSetting, job_id)
            if row is not None:
                await db.delete(row)
        await db.commit()


def test_maintenance_tasks_are_registered_as_administrator_jobs_with_bounded_cadence():
    for job_id, default in (("library_backups", 24 * 60), ("trash_sweep", 6 * 60)):
        spec = jobs.JOBS[job_id]
        assert spec.user_id is None and spec.default_enabled
        assert spec.min_interval_minutes <= default <= spec.max_interval_minutes
    # Sessions and trash cadence are due only once their interval has elapsed.
    assert not jobs.is_due(True, 1000, 360, 1000 + 359 * 60)
    assert jobs.is_due(True, 1000, 360, 1000 + 360 * 60)
    assert not jobs.is_due(False, None, 360, 10**9)


async def _start_and_wait(job_id):
    spec = jobs.JOBS[job_id]
    tasks = asyncio.all_tasks()
    assert spec.start("needed") == {"running": True}
    assert spec.is_running()
    await next(iter(asyncio.all_tasks() - tasks))
    assert not spec.is_running()
    async with SessionLocal() as db:
        return await jobs.describe(db, spec)


async def test_backup_run_records_counts_and_a_partial_failure(monkeypatch):
    monkeypatch.setattr(
        jobs, "run_all_backups", AsyncMock(return_value={"accounts": 3, "written": 2, "failed": 0})
    )
    ok = await _start_and_wait("library_backups")
    assert ok["last_summary"] == "2 snapshot(s) written for 3 account(s)"
    assert "error" not in ok["last_result"]
    monkeypatch.setattr(
        jobs, "run_all_backups", AsyncMock(return_value={"accounts": 3, "written": 2, "failed": 1})
    )
    bad = await _start_and_wait("library_backups")
    assert bad["last_result"]["completed"] is False
    assert bad["last_summary"].endswith("1 failed")


async def test_sweep_exception_is_recorded_without_detail(monkeypatch):
    monkeypatch.setattr(
        jobs, "run_sweep_once", AsyncMock(side_effect=RuntimeError("secret upstream detail"))
    )
    row = await _start_and_wait("trash_sweep")
    assert row["last_result"] == {"completed": False, "error": "The run failed"}
    assert "secret" not in str(row)


async def test_sweep_pass_reports_failed_parts(monkeypatch):
    monkeypatch.setattr(sweep, "purge_expired_trash", AsyncMock(side_effect=RuntimeError("x")))
    monkeypatch.setattr(sweep, "purge_expired_sessions", AsyncMock(return_value=2))
    result = await sweep.run_sweep_once()
    assert result == {"failed_parts": 1, "expired_sessions_removed": 2}


async def test_runs_do_not_overlap(monkeypatch):
    release = asyncio.Event()

    async def work():
        calls.append(1)
        await release.wait()
        return {}

    calls = []
    monkeypatch.setattr(jobs, "run_sweep_once", work)
    spec = jobs.JOBS["trash_sweep"]
    spec.start("needed")
    spec.start("needed")
    await asyncio.sleep(0.05)
    assert len(calls) == 1
    release.set()
    while spec.is_running():
        await asyncio.sleep(0.01)


async def test_backup_status_follows_the_administrator_schedule():
    async with SessionLocal() as db:
        row = await jobs.get_setting(db, jobs.JOBS["library_backups"])
        row.enabled, row.interval_minutes = False, 90
        await db.commit()
        status = await backup_status(User(id=uuid4()), db)
    assert status.enabled is False and status.interval_hours == 2


async def test_maintenance_jobs_are_admin_only_in_the_route(monkeypatch):
    # Personal lookups never resolve an administrator job id.
    async with SessionLocal() as db:
        with pytest.raises(HTTPException) as caught:
            await job_routes._spec("library_backups", db, uuid4())
    assert caught.value.status_code == 404
