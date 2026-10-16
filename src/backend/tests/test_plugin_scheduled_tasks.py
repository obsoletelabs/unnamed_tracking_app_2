"""Schedule contracts and persisted installation/user permission boundaries."""

import asyncio
import io
import json
import time
import zipfile
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import delete, select

from src.api.routes import jobs as job_routes
from src.database.models.job_setting import JobSetting
from src.database.models.plugin_permissions import PluginPermissionGrant
from src.database.models.user import User
from src.database.session import SessionLocal
from src.features import job_runs, jobs, plugin_jobs
from src.features.job_runs import job_lease
from src.plugin_api.contracts import PluginManifest, PluginScheduledTask
from src.plugin_api.runtime_client import PluginRuntimeUnavailable
from src.plugin_api.updates import (
    PackageFormatError,
    PluginPackageVerifier,
    canonical_payload_digest,
)


def manifest(**changes):
    """One v1.1 action declaration; no plugin implementation or private imports."""
    return {
        "plugin_id": "example.tasks",
        "name": "Example tasks",
        "version": "1.1.0",
        "api_contract_version": "1.1.0",
        "entrypoint": "plugin:main",
        "sdk_version_range": "^1.1.0",
        "application_version_range": "*",
        "integrity": {"sha256": "0" * 64},
        "capabilities": [{"name": "tasks.background", "version": 1}],
        "permissions": [
            {
                "capability": {"name": "tasks.background", "version": 1},
                "rationale": "Run a reviewed task.",
            }
        ],
        "scheduled_tasks": [{"id": "summary", "name": "Summary", "action_id": "summarize"}],
        **changes,
    }


@pytest.mark.parametrize(
    "changes",
    [
        {"api_contract_version": "1.0.0"},
        {"capabilities": [], "permissions": []},
        {"scheduled_tasks": [{"id": "summary", "name": "Summary", "action_id": "summarize"}] * 2},
        {"scheduled_tasks": [{"id": "summary", "name": "Summary", "action_id": "summarize"}] * 33},
    ],
)
def test_tasks_require_v11_explicit_permission_unique_bounded_declarations(changes):
    with pytest.raises(ValidationError):
        PluginManifest.model_validate(manifest(**changes))


@pytest.mark.parametrize(
    "changes",
    [
        {"default_interval_minutes": 1},
        {"max_interval_minutes": 30},
        {"min_interval_minutes": 0},
        {"max_interval_minutes": 43_201},
        {"default_interval_minutes": True},
        {"default_interval_minutes": 1.5},
    ],
)
def test_task_periods_are_strict_and_within_declared_bounds(changes):
    with pytest.raises(ValidationError):
        PluginScheduledTask(id="summary", name="Summary", action_id="summarize", **changes)


@pytest.mark.parametrize(
    "action",
    [
        None,
        {
            "id": "summarize",
            "label": "Summary",
            "handler": "plugin:summary",
            "confirmation": "Confirm this destructive action",
        },
        {"id": "summarize", "label": "Summary"},
    ],
)
def test_package_verifier_rejects_missing_or_confirmed_scheduled_actions(action, tmp_path):
    document = {
        "api_contract_version": "1.1.0",
        "plugin_id": "example.tasks",
        "title": "Tasks",
        "actions": [action] if action else [],
    }
    payload = [("ui.json", json.dumps(document).encode())]
    package = io.BytesIO()
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr(
            "manifest.json",
            json.dumps(manifest(integrity={"sha256": canonical_payload_digest(payload)})),
        )
        archive.writestr("payload/ui.json", payload[0][1])
    path = tmp_path / "task.utp"
    path.write_bytes(package.getvalue())
    with pytest.raises(PackageFormatError, match="scheduled task targets"):
        PluginPackageVerifier(require_signature=False).inspect(path)


@pytest.fixture
async def scheduled(monkeypatch):
    """Real grants and job rows, with only the isolated runtime transport substituted."""
    installation_id, actor_id, other_id = uuid4(), uuid4(), uuid4()
    task = {"id": "summary", "name": "Summary", "action_id": "summarize"}
    installed = {
        "plugin_id": "example.tasks",
        "name": "Tasks example",
        "installation_id": str(installation_id),
        "background_user_id": str(actor_id),
        "api_contract_version": "1.1.0",
        "enabled": True,
        "compatible": True,
        "status": "running",
        "health": "healthy",
        "scheduled_tasks": [task],
    }
    transport = AsyncMock()
    transport.plugins.return_value = [installed]
    transport.plugin_ui.return_value = {
        "api_contract_version": "1.1.0",
        "plugin_id": "example.tasks",
        "title": "Tasks",
        "actions": [
            {
                "id": "summarize",
                "label": "Summary",
                "handler": "plugin:summary",
                "capability": {"name": "games.read", "version": 1},
            }
        ],
    }
    transport.run_scheduled_task.return_value = {
        "completed": True,
        "summary": "Read 3 games",
        "private": "not persisted",
    }
    monkeypatch.setattr(plugin_jobs, "client", transport)
    monkeypatch.setattr(plugin_jobs, "_last_inventory", [])
    monkeypatch.setattr(plugin_jobs, "_running", {})
    async with SessionLocal() as db:
        for user_id in (actor_id, other_id):
            db.add(
                User(
                    id=user_id,
                    username="task-" + str(user_id),
                    email=f"{user_id}@example.invalid",
                    password_hash="unused",
                    is_admin=True,
                )
            )
        await db.flush()
        for capability in ("tasks.background", "games.read"):
            db.add(
                PluginPermissionGrant(
                    plugin_id="example.tasks",
                    installation_id=installation_id,
                    user_id=actor_id,
                    capability=capability,
                    capability_version=1,
                )
            )
        await db.commit()
    try:
        yield installed, transport, installation_id, actor_id, other_id
    finally:
        running = list(plugin_jobs._running.values())
        for task_run in running:
            task_run.cancel()
        await asyncio.gather(*running, return_exceptions=True)
        async with SessionLocal() as db:
            await db.execute(
                delete(JobSetting).where(
                    JobSetting.job_id == plugin_jobs.task_job_id(installation_id, "summary")
                )
            )
            await db.execute(delete(User).where(User.id.in_([actor_id, other_id])))
            await db.commit()


@pytest.mark.asyncio
async def test_tasks_start_off_and_keep_schedules_results_and_no_overlap(scheduled):
    installed, transport, installation_id, _, _ = scheduled
    started, release = asyncio.Event(), asyncio.Event()

    async def complete_after_release(*_args):
        started.set()
        await release.wait()
        return {"completed": True, "summary": "Read 3 games", "private": "not persisted"}

    transport.run_scheduled_task.side_effect = complete_after_release
    job_id = plugin_jobs.task_job_id(installation_id, "summary")
    assert len(job_id) <= 50
    assert job_id != plugin_jobs.task_job_id(uuid4(), "summary")
    async with SessionLocal() as db:
        listed = next(job for job in await job_routes.list_jobs(db) if job["id"] == job_id)
        assert (
            listed["available"]
            and not listed["enabled"]
            and listed["plugin_id"] == installed["plugin_id"]
        )
        await job_routes.update_job(
            job_id, job_routes.JobUpdate(enabled=True, interval_minutes=15), db
        )
        with pytest.raises(HTTPException) as invalid:
            await job_routes.update_job(job_id, job_routes.JobUpdate(interval_minutes=1), db)
        assert invalid.value.status_code == 400
        assert await job_routes.run_job(job_id, None, db) == {"running": True}
        await started.wait()
        assert await job_routes.run_job(job_id, None, db) == {"running": True}
    release.set()
    await asyncio.gather(*list(plugin_jobs._running.values()))
    transport.run_scheduled_task.assert_awaited_once_with(
        "example.tasks", str(installation_id), "summary", "manual"
    )
    async with SessionLocal() as db:
        row = await db.get(JobSetting, job_id)
        assert row.enabled and row.interval_minutes == 15 and row.last_run_at
        assert row.last_result == {"completed": True, "summary": "Read 3 games"}
        again = next(job for job in await job_routes.list_jobs(db) if job["id"] == job_id)
        assert again["last_summary"] == "Read 3 games"


@pytest.mark.asyncio
async def test_other_account_grants_cannot_replace_the_worker_grant(scheduled):
    _, transport, installation_id, actor_id, other_id = scheduled
    async with SessionLocal() as db:
        await db.execute(
            delete(PluginPermissionGrant).where(
                PluginPermissionGrant.installation_id == installation_id,
                PluginPermissionGrant.capability == "tasks.background",
            )
        )
        db.add(
            PluginPermissionGrant(
                plugin_id="example.tasks",
                installation_id=installation_id,
                user_id=other_id,
                capability="tasks.background",
                capability_version=1,
            )
        )
        await db.commit()
        spec = (await plugin_jobs.get_plugin_jobs(db))[0]
        assert not spec.available and "Background permission" in spec.unavailable_reason
        with pytest.raises(HTTPException) as denied:
            await job_routes.run_job(spec.id, None, db)
        assert denied.value.status_code == 409
        transport.run_scheduled_task.assert_not_awaited()
        db.add(
            PluginPermissionGrant(
                plugin_id="example.tasks",
                installation_id=installation_id,
                user_id=actor_id,
                capability="tasks.background",
                capability_version=1,
            )
        )
        await db.commit()
        assert (await plugin_jobs.get_plugin_jobs(db))[0].available


@pytest.mark.asyncio
async def test_revoke_action_permission_stop_legacy_and_outage_pause_tasks(scheduled):
    installed, transport, installation_id, _, _ = scheduled
    async with SessionLocal() as db:
        assert (await plugin_jobs.get_plugin_jobs(db))[0].available
        grant = await db.scalar(
            select(PluginPermissionGrant).where(
                PluginPermissionGrant.installation_id == installation_id,
                PluginPermissionGrant.capability == "games.read",
            )
        )
        grant.revoked_at = int(time.time())
        await db.commit()
        assert "games.read" in (await plugin_jobs.get_plugin_jobs(db))[0].unavailable_reason
        grant.revoked_at = None
        await db.commit()
        installed["status"] = "stopped"
        assert not (await plugin_jobs.get_plugin_jobs(db))[0].available
        installed["status"] = "running"
        installed["legacy_compatibility"] = True
        assert not await plugin_jobs.get_plugin_jobs(db)
        installed["legacy_compatibility"] = False
        assert (await plugin_jobs.get_plugin_jobs(db))[0].available
        transport.plugins.side_effect = PluginRuntimeUnavailable("offline")
        assert (
            "runtime is unavailable"
            in (await plugin_jobs.get_plugin_jobs(db))[0].unavailable_reason
        )


@pytest.mark.asyncio
async def test_revocation_between_start_and_dispatch_is_rechecked(scheduled):
    _, transport, installation_id, _, _ = scheduled
    async with SessionLocal() as db:
        spec = (await plugin_jobs.get_plugin_jobs(db))[0]
        await db.execute(
            delete(PluginPermissionGrant).where(
                PluginPermissionGrant.installation_id == installation_id,
                PluginPermissionGrant.capability == "tasks.background",
            )
        )
        await db.commit()
    spec.start("needed")
    await asyncio.gather(*list(plugin_jobs._running.values()))
    transport.run_scheduled_task.assert_not_awaited()


def test_personal_tasks_require_the_additive_contract():
    declaration = {"id": "summary", "name": "Summary", "action_id": "summarize", "scope": "user"}
    with pytest.raises(ValidationError, match="v1.1.11"):
        PluginManifest.model_validate(manifest(scheduled_tasks=[declaration]))
    parsed = PluginManifest.model_validate(
        manifest(api_contract_version="1.1.11", scheduled_tasks=[declaration])
    )
    assert parsed.scheduled_tasks[0].scope == "user"


@pytest.mark.asyncio
async def test_personal_schedules_use_non_admin_owner_and_cannot_be_managed_by_another_user(
    scheduled,
):
    installed, transport, installation_id, actor_id, other_id = scheduled
    installed["api_contract_version"] = "1.1.11"
    installed["scheduled_tasks"][0]["scope"] = "user"
    transport.plugin_ui.return_value["api_contract_version"] = "1.1.11"
    transport.run_scheduled_task.return_value = {
        "completed": False,
        "summary": "Inventory saved",
        "continue_after_seconds": 1,
        "private": "not persisted",
    }
    job_id = plugin_jobs.task_job_id(installation_id, "summary", actor_id)
    assert len(job_id) <= 50
    assert job_id != plugin_jobs.task_job_id(installation_id, "summary", other_id)
    async with SessionLocal() as db:
        actor = await db.get(User, actor_id)
        actor.is_admin = False
        await db.commit()
        personal = await job_routes.list_user_jobs(db, actor)
        assert len(personal) == 1 and personal[0]["available"] and not personal[0]["enabled"]
        assert await plugin_jobs.get_plugin_jobs(db) == []
        other = await db.get(User, other_id)
        with pytest.raises(HTTPException) as denied:
            await job_routes.update_user_job(job_id, job_routes.JobUpdate(enabled=True), db, other)
        assert denied.value.status_code == 404
        with pytest.raises(HTTPException) as hidden:
            await job_routes.run_user_job("media_refresh", db, actor)
        assert hidden.value.status_code == 404
        await job_routes.update_user_job(
            job_id, job_routes.JobUpdate(enabled=True, interval_minutes=60), db, actor
        )
        assert (await db.get(JobSetting, job_id)).user_id == actor_id
        assert [spec.id for spec in await jobs.get_scheduled_user_jobs(db)] == [job_id]
        assert await job_routes.run_user_job(job_id, db, actor) == {"running": True}
    await asyncio.gather(*list(plugin_jobs._running.values()))
    transport.run_scheduled_task.assert_awaited_once_with(
        "example.tasks", str(installation_id), "summary", "manual", user_id=str(actor_id)
    )
    async with SessionLocal() as db:
        row = await db.get(JobSetting, job_id)
        assert row.last_result == {
            "completed": False,
            "summary": "Inventory saved",
            "continue_after_seconds": 60,
        }
        row.enabled = False
        await db.commit()
        assert await jobs.get_scheduled_user_jobs(db) == []


@pytest.mark.asyncio
async def test_personal_background_access_is_rechecked_for_the_target_account(scheduled):
    installed, transport, installation_id, actor_id, _ = scheduled
    installed["scheduled_tasks"][0]["scope"] = "user"
    installed["api_contract_version"] = "1.1.11"
    async with SessionLocal() as db:
        spec = (await plugin_jobs.get_plugin_jobs(db, actor_id))[0]
        grant = await db.scalar(
            select(PluginPermissionGrant).where(
                PluginPermissionGrant.installation_id == installation_id,
                PluginPermissionGrant.capability == "games.read",
            )
        )
        grant.revoked_at = int(time.time())
        await db.commit()
    spec.start("all")
    await asyncio.gather(*list(plugin_jobs._running.values()))
    transport.run_scheduled_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_disabled_personal_schedule_cannot_start_a_queued_step(scheduled):
    installed, transport, _, actor_id, _ = scheduled
    installed["scheduled_tasks"][0]["scope"] = "user"
    installed["api_contract_version"] = "1.1.11"
    async with SessionLocal() as db:
        spec = (await plugin_jobs.get_plugin_jobs(db, actor_id))[0]
    spec.start("needed")
    await asyncio.gather(*list(plugin_jobs._running.values()))
    transport.run_scheduled_task.assert_not_awaited()


# --- Durable run state: overlap lease, controls, backoff and revocation -------------------------


def _row(**changes):
    values = {
        "job_id": "user-plugin:fixture",
        "enabled": True,
        "paused": False,
        "cancel_requested": False,
        "status": "idle",
        "failure_count": 0,
        "interval_minutes": 60,
        "last_run_at": 1000,
        "retry_at": None,
        "lease_expires_at": None,
        "last_result": None,
        "last_success_at": None,
    }
    return JobSetting(**{**values, **changes})


def test_failures_back_off_honour_rate_limits_and_stop_retrying():
    row = _row()
    job_runs.apply_outcome(row, {"completed": False, "summary": "Epic failed"}, 2000)
    assert (row.status, row.failure_count, row.retry_at) == ("failed", 1, 2060)
    job_runs.apply_outcome(
        row, {"completed": False, "summary": "Rate limited", "retry_after_seconds": 900}, 3000
    )
    assert (row.failure_count, row.retry_at) == (2, 3900)
    for _ in range(job_runs.MAX_RETRIES):
        job_runs.apply_outcome(row, {"completed": False}, 4000)
    assert row.retry_at is None and row.failure_count > job_runs.MAX_RETRIES
    job_runs.apply_outcome(row, {"completed": True, "summary": "ok"}, 5000)
    assert (row.status, row.failure_count, row.last_success_at) == ("completed", 0, 5000)
    assert job_runs.bounded_delay(True) is None and job_runs.bounded_delay(5) == 60


def test_partial_steps_continue_quickly_and_keep_the_last_success():
    row = _row(last_success_at=500)
    public = job_runs.apply_outcome(
        row, {"completed": False, "summary": "saved", "continue_after_seconds": 60}, 2000
    )
    assert (row.status, row.failure_count, row.last_success_at) == ("partial", 0, 500)
    row.last_run_at, row.last_result = 2000, public
    assert job_runs.next_run_at(row, 2001) == 2060
    assert not job_runs.is_due(row, 2059) and job_runs.is_due(row, 2060)


def test_pause_disable_and_leases_gate_the_scheduler():
    assert job_runs.is_due(_row(last_run_at=None), 10)
    assert not job_runs.is_due(_row(paused=True, last_run_at=None), 10)
    assert not job_runs.is_due(_row(enabled=False, last_run_at=None), 10)
    assert not job_runs.is_due(_row(last_run_at=None, lease_expires_at=20), 10)
    assert job_runs.is_due(_row(last_run_at=None, lease_expires_at=5), 10)
    assert job_runs.next_run_at(_row(enabled=False), 10) is None


def test_cancel_discards_only_the_continuation_and_a_running_cancel_wins():
    row = _row(last_result={"completed": False, "summary": "saved", "continue_after_seconds": 60})
    job_runs.apply_control(row, "cancel", running=False)
    assert row.status == "cancelled" and "continue_after_seconds" not in row.last_result
    done = _row(last_result={"completed": True, "summary": "All saved"})
    job_runs.apply_control(done, "cancel", running=False)
    assert done.last_result["summary"] == "All saved"
    live = _row()
    job_runs.apply_control(live, "cancel", running=True)
    public = job_runs.apply_outcome(
        live, {"completed": False, "continue_after_seconds": 60, "summary": "x"}, 9
    )
    assert live.status == "cancelled" and not live.cancel_requested
    assert "continue_after_seconds" not in public and live.retry_at is None


@pytest.mark.asyncio
async def test_lease_blocks_overlap_and_a_crashed_holder_expires(scheduled):
    _, _, installation_id, _, _ = scheduled
    spec = plugin_jobs._job_spec(
        {"plugin_id": "example.tasks"},
        installation_id,
        PluginScheduledTask(id="summary", name="S", action_id="summarize"),
        "",
    )
    async with job_lease(spec) as first:
        assert first
        async with job_lease(spec) as second:
            assert not second
        async with SessionLocal() as db:  # the holder's process died without releasing
            row = await db.get(JobSetting, spec.id)
            row.lease_expires_at = int(time.time()) - 1
            await db.commit()
        async with job_lease(spec) as after_restart:
            assert after_restart
    async with SessionLocal() as db:
        assert (await db.get(JobSetting, spec.id)).lease_token is None


@pytest.fixture
async def personal(scheduled):
    """The scheduled fixture, switched to a non-administrator account's personal task."""
    installed, transport, installation_id, actor_id, other_id = scheduled
    installed["api_contract_version"] = "1.1.11"
    installed["scheduled_tasks"][0]["scope"] = "user"
    transport.plugin_ui.return_value["api_contract_version"] = "1.1.11"
    job_id = plugin_jobs.task_job_id(installation_id, "summary", actor_id)
    async with SessionLocal() as db:
        actor, other = await db.get(User, actor_id), await db.get(User, other_id)
        actor.is_admin = other.is_admin = False
        await job_routes.update_user_job(job_id, job_routes.JobUpdate(enabled=True), db, actor)
    return installed, transport, job_id, actor_id, other_id


async def _run(job_id, user_id):
    async with SessionLocal() as db:
        user = await db.get(User, user_id)
        await job_routes.run_user_job(job_id, db, user)
    await asyncio.gather(*list(plugin_jobs._running.values()))


@pytest.mark.asyncio
async def test_pause_blocks_manual_and_scheduled_steps_until_resumed(personal):
    _, transport, job_id, actor_id, _ = personal
    transport.run_scheduled_task.return_value = {
        "completed": False,
        "summary": "Inventory saved",
        "continue_after_seconds": 60,
    }
    async with SessionLocal() as db:
        actor = await db.get(User, actor_id)
        paused = await job_routes.control_user_job(job_id, "pause", db, actor)
        assert paused["paused"] and paused["status"] == "paused" and paused["next_run_at"] is None
        with pytest.raises(HTTPException) as refused:
            await job_routes.run_user_job(job_id, db, actor)
        assert refused.value.status_code == 409
        spec = (await plugin_jobs.get_plugin_jobs(db, actor_id))[0]
    spec.start("needed")
    await asyncio.gather(*list(plugin_jobs._running.values()))
    transport.run_scheduled_task.assert_not_awaited()
    async with SessionLocal() as db:
        resumed = await job_routes.control_user_job(job_id, "resume", db, actor)
        assert not resumed["paused"] and resumed["next_run_at"] is not None
    await _run(job_id, actor_id)
    transport.run_scheduled_task.assert_awaited_once()
    async with SessionLocal() as db:
        saved = await job_routes.control_user_job(job_id, "pause", db, actor)
        assert saved["last_result"]["continue_after_seconds"] == 60
        assert saved["status"] == "paused" and saved["last_summary"] == "Inventory saved"


@pytest.mark.asyncio
async def test_cancel_during_a_step_keeps_the_checkpoint_and_stops_the_run(personal):
    _, transport, job_id, actor_id, _ = personal
    started, release = asyncio.Event(), asyncio.Event()

    async def step(*_args, **_kwargs):
        started.set()
        await release.wait()
        return {"completed": False, "summary": "saved", "continue_after_seconds": 60}

    transport.run_scheduled_task.side_effect = step
    async with SessionLocal() as db:
        actor = await db.get(User, actor_id)
        await job_routes.run_user_job(job_id, db, actor)
        await started.wait()
        view = await job_routes.control_user_job(job_id, "cancel", db, actor)
        assert view["running"]
    release.set()
    await asyncio.gather(*list(plugin_jobs._running.values()))
    async with SessionLocal() as db:
        row = await db.get(JobSetting, job_id)
        assert row.status == "cancelled" and not row.cancel_requested and row.enabled
        assert row.last_result["summary"].startswith("Cancelled")
        assert job_runs.next_run_at(row, row.last_run_at) == row.last_run_at + 3600


@pytest.mark.asyncio
async def test_manual_and_scheduled_starts_cannot_overlap_across_processes(personal):
    _, transport, job_id, actor_id, _ = personal
    async with SessionLocal() as db:
        spec = (await plugin_jobs.get_plugin_jobs(db, actor_id))[0]
    async with job_lease(spec) as held:  # another host process owns the running inventory
        assert held
        async with SessionLocal() as db:
            view = await job_routes.list_user_jobs(db, await db.get(User, actor_id))
            assert view[0]["running"]
        spec.start("needed")
        await asyncio.gather(*list(plugin_jobs._running.values()))
    transport.run_scheduled_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_failure_status_is_safe_and_rate_limits_delay_the_retry(personal):
    _, transport, job_id, actor_id, _ = personal
    transport.run_scheduled_task.return_value = {
        "completed": False,
        "summary": "Epic is rate limiting requests.",
        "retry_after_seconds": 1200,
        "token": "never stored",
    }
    await _run(job_id, actor_id)
    async with SessionLocal() as db:
        row = await db.get(JobSetting, job_id)
        assert row.status == "failed" and row.failure_count == 1
        assert row.retry_at - row.last_run_at == 1200
        assert "token" not in row.last_result and row.last_success_at is None
    transport.run_scheduled_task.side_effect = PluginRuntimeUnavailable("offline")
    await _run(job_id, actor_id)
    async with SessionLocal() as db:
        assert (await db.get(JobSetting, job_id)).failure_count == 2


@pytest.mark.asyncio
async def test_provider_disconnect_turns_the_schedule_off_and_retains_the_checkpoint(personal):
    _, transport, job_id, actor_id, _ = personal
    transport.run_scheduled_task.return_value = {
        "completed": False,
        "summary": "Connect your Epic account first.",
        "disable_schedule": True,
    }
    await _run(job_id, actor_id)
    async with SessionLocal() as db:
        row = await db.get(JobSetting, job_id)
        assert not row.enabled and row.status == "disabled" and row.retry_at is None
        assert await jobs.get_scheduled_user_jobs(db) == []


@pytest.mark.asyncio
async def test_revocation_disables_but_outages_and_other_accounts_do_not(personal):
    _, transport, job_id, actor_id, other_id = personal
    async with SessionLocal() as db:
        transport.plugins.side_effect = PluginRuntimeUnavailable("offline")
        await jobs.disable_revoked_schedules(db)
        assert (await db.get(JobSetting, job_id)).enabled  # outage: paused, not disabled
        transport.plugins.side_effect = None
    async with SessionLocal() as db:
        await db.execute(
            delete(PluginPermissionGrant).where(
                PluginPermissionGrant.capability == "tasks.background",
                PluginPermissionGrant.user_id == actor_id,
            )
        )
        await db.commit()
        await jobs.disable_revoked_schedules(db)
        row = await db.get(JobSetting, job_id)
        assert not row.enabled and row.status == "disabled"
        assert "Autosync was turned off" in row.last_result["summary"]
        with pytest.raises(HTTPException) as unavailable:
            await job_routes.update_user_job(
                job_id, job_routes.JobUpdate(enabled=True), db, await db.get(User, actor_id)
            )
        assert unavailable.value.status_code == 409


@pytest.mark.asyncio
async def test_uninstalled_plugin_disables_its_schedule_only_with_a_live_runtime(personal):
    installed, transport, job_id, actor_id, _ = personal
    transport.plugins.return_value = []
    async with SessionLocal() as db:
        await jobs.disable_revoked_schedules(db)
        assert not (await db.get(JobSetting, job_id)).enabled


@pytest.mark.asyncio
async def test_other_accounts_cannot_control_a_personal_task(personal):
    _, _, job_id, _, other_id = personal
    async with SessionLocal() as db:
        other = await db.get(User, other_id)
        for control in ("pause", "resume", "cancel"):
            with pytest.raises(HTTPException) as denied:
                await job_routes.control_user_job(job_id, control, db, other)
            assert denied.value.status_code == 404


@pytest.mark.asyncio
async def test_a_checkpoint_survives_a_host_restart_and_resumes_when_due(personal):
    _, transport, job_id, actor_id, _ = personal
    transport.run_scheduled_task.return_value = {
        "completed": False,
        "summary": "Inventory saved",
        "continue_after_seconds": 60,
    }
    await _run(job_id, actor_id)
    plugin_jobs._running.clear()  # a new process knows nothing of the previous run
    async with SessionLocal() as db:
        row = await db.get(JobSetting, job_id)
        assert not job_runs.is_due(row, row.last_run_at + 59)
        assert job_runs.is_due(row, row.last_run_at + 60)
        assert row.status == "partial" and row.lease_token is None
