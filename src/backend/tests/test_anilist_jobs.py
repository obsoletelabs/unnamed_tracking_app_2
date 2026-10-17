"""The AniList import schedule is a personal task owned by one account."""

import asyncio
import importlib
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import delete, select, text

from src.api.routes import jobs as job_routes
from src.core.preferences import save_preferences, validate_preference
from src.database.models.job_setting import JobSetting
from src.database.models.user import User
from src.database.models.user_preferences import UserPreferences
from src.database.session import SessionLocal
from src.features import anilist_jobs, jobs, plugin_jobs
from src.features.metadata.anime.anilist import AniListError


@pytest.fixture
async def accounts(monkeypatch):
    actor_id, other_id = uuid4(), uuid4()
    transport = AsyncMock()
    transport.plugins.return_value = []
    monkeypatch.setattr(plugin_jobs, "client", transport)
    monkeypatch.setattr(anilist_jobs, "_running", {})
    async with SessionLocal() as db:
        for identity in (actor_id, other_id):
            db.add(
                User(
                    id=identity,
                    username=f"al-{identity}",
                    email=f"{identity}@example.invalid",
                    password_hash="unused",
                    is_admin=False,
                )
            )
        await db.commit()
    yield actor_id, other_id
    tasks = list(anilist_jobs._running.values())
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    async with SessionLocal() as db:
        await db.execute(delete(User).where(User.id.in_([actor_id, other_id])))
        await db.commit()


def _anilist(listed):
    return next(task for task in listed if task["id"].startswith("user-anilist:"))


async def test_task_is_listed_off_and_unavailable_until_a_username_is_saved(accounts):
    actor_id, other_id = accounts
    async with SessionLocal() as db:
        actor = await db.get(User, actor_id)
        task = _anilist(await job_routes.list_user_jobs(db, actor))
        assert task["id"] == anilist_jobs.anilist_job_id(actor_id)
        assert task["id"] != anilist_jobs.anilist_job_id(other_id)
        assert not task["enabled"] and task["interval_minutes"] == 1440
        assert not task["available"] and "username" in task["unavailable_reason"]
        assert (task["min_interval_minutes"], task["max_interval_minutes"]) == (60, 43200)
        with pytest.raises(HTTPException) as failure:
            await job_routes.run_user_job(task["id"], db, actor)
        assert failure.value.status_code == 409
        await save_preferences(db, actor_id, {"anilist_import_username": " someone "})
        task = _anilist(await job_routes.list_user_jobs(db, actor))
        assert task["available"] and task["unavailable_reason"] == ""
        updated = await job_routes.update_user_job(
            task["id"], job_routes.JobUpdate(enabled=True, interval_minutes=360), db, actor
        )
        assert updated["enabled"] and updated["interval_minutes"] == 360
        with pytest.raises(HTTPException) as failure:
            await job_routes.update_user_job(
                task["id"], job_routes.JobUpdate(interval_minutes=30), db, actor
            )
        assert failure.value.status_code == 400
        # Another account can neither see nor change it, and it is never an admin job.
        other = await db.get(User, other_id)
        assert all(t["id"] != task["id"] for t in await job_routes.list_user_jobs(db, other))
        with pytest.raises(HTTPException) as failure:
            await job_routes.update_user_job(
                task["id"], job_routes.JobUpdate(enabled=False), db, other
            )
        assert failure.value.status_code == 404
        assert task["id"] not in {j["id"] for j in await job_routes.list_jobs(db)}


async def test_scheduled_run_imports_with_saved_preferences_and_records_the_result(
    accounts, monkeypatch
):
    actor_id, _ = accounts
    calls = []

    async def fake_import(db, user_id, username, update_existing):
        calls.append((user_id, username, update_existing))
        return {"fetched": 3, "created": 2, "updated": 1, "skipped": 0, "errors": []}

    monkeypatch.setattr(anilist_jobs, "import_anilist_library", fake_import)
    async with SessionLocal() as db:
        await save_preferences(
            db,
            actor_id,
            {"anilist_import_username": "someone", "anilist_import_update_existing": True},
        )
        spec = (await anilist_jobs.get_anilist_jobs(db, actor_id))[0]
    spec.start("needed")
    await asyncio.gather(*anilist_jobs._running.values())
    assert calls == [(actor_id, "someone", True)]
    async with SessionLocal() as db:
        row = await db.get(JobSetting, spec.id)
        assert row.user_id == actor_id and row.last_run_at
        assert row.last_result["completed"] is True
        assert spec.summarize(row.last_result) == "3 fetched, 2 added, 1 updated, 0 skipped."


async def test_failed_import_records_a_safe_message_and_releases_the_task(accounts, monkeypatch):
    actor_id, _ = accounts
    monkeypatch.setattr(
        anilist_jobs,
        "import_anilist_library",
        AsyncMock(side_effect=AniListError("secret upstream detail")),
    )
    async with SessionLocal() as db:
        await save_preferences(db, actor_id, {"anilist_import_username": "someone"})
        spec = (await anilist_jobs.get_anilist_jobs(db, actor_id))[0]
    spec.start("all")
    assert spec.is_running()
    await asyncio.gather(*anilist_jobs._running.values())
    assert not spec.is_running()
    async with SessionLocal() as db:
        row = await db.get(JobSetting, spec.id)
        assert row.last_result["completed"] is False
        assert "secret" not in spec.summarize(row.last_result)
        assert "AniList" in spec.summarize(row.last_result)


async def test_only_the_registered_task_schedules_the_import_with_no_duplicate(accounts):
    actor_id, _ = accounts
    async with SessionLocal() as db:
        await save_preferences(db, actor_id, {"anilist_import_username": "someone"})
        specs = await jobs.get_user_jobs(db, actor_id)
        assert [s.id for s in specs if "anilist" in s.id] == [anilist_jobs.anilist_job_id(actor_id)]
        assert not hasattr(jobs, "_run_due_anilist_imports")
        row = await db.get(
            UserPreferences,
            (
                await db.scalar(
                    select(UserPreferences.id).where(UserPreferences.user_id == actor_id)
                )
            ),
        )
        assert "anilist_import_enabled" not in row.data


def test_retired_schedule_preferences_are_rejected():
    for key in (
        "anilist_import_enabled",
        "anilist_import_interval_minutes",
        "anilist_import_last_run_at",
        "anilist_import_last_attempt_at",
        "anilist_import_last_error",
    ):
        with pytest.raises(ValueError):
            validate_preference(key, 60)


async def test_migration_moves_saved_schedules_and_downgrade_restores_them(accounts):
    actor_id, other_id = accounts
    migration = importlib.import_module(
        "src.database.migrations.versions.a55b7c1d9e20_move_anilist_import_schedule_to_jobs"
    )
    async with SessionLocal() as db:
        db.add_all(
            [
                UserPreferences(
                    user_id=actor_id,
                    data={
                        "anilist_import_username": "someone",
                        "anilist_import_enabled": True,
                        "anilist_import_interval_minutes": 360,
                        "anilist_import_last_run_at": 1700000000,
                        # A later failed attempt keeps the cadence; its report is retired.
                        "anilist_import_last_attempt_at": 1700000600,
                        "anilist_import_last_error": "Could not reach AniList.",
                        "theme": "keep",
                    },
                ),
                UserPreferences(user_id=other_id, data={"theme": "untouched"}),
            ]
        )
        await db.commit()

        def run(sync_conn):
            from alembic.migration import MigrationContext
            from alembic.operations import Operations

            with Operations.context(MigrationContext.configure(sync_conn)):
                migration.upgrade()

        conn = await db.connection()
        await conn.run_sync(run)
        job_id = anilist_jobs.anilist_job_id(actor_id)
        row = (await db.execute(select(JobSetting).where(JobSetting.job_id == job_id))).scalar_one()
        assert (row.user_id, row.enabled, row.interval_minutes, row.last_run_at) == (
            actor_id,
            True,
            360,
            1700000600,
        )
        data = await db.scalar(
            select(UserPreferences.data).where(UserPreferences.user_id == actor_id)
        )
        assert data == {"anilist_import_username": "someone", "theme": "keep"}
        assert await db.get(JobSetting, anilist_jobs.anilist_job_id(other_id)) is None

        def undo(sync_conn):
            from alembic.migration import MigrationContext
            from alembic.operations import Operations

            with Operations.context(MigrationContext.configure(sync_conn)):
                migration.downgrade()

        await conn.run_sync(undo)
        data = await db.scalar(
            text("SELECT data FROM user_preferences WHERE user_id = :u"), {"u": actor_id}
        )
        assert data["anilist_import_enabled"] is True
        assert data["anilist_import_interval_minutes"] == 360
        assert (
            await db.scalar(select(JobSetting.job_id).where(JobSetting.job_id == job_id))
        ) is None
        await db.rollback()
