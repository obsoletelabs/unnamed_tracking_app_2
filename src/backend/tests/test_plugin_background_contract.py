"""Host background-task contract against the actual signed Epic and Jellyfin packages.

The packages come from the companion plugin repository (`UTAPP_PLUGINS_REPO`, default
`/home/user/unnamed_tracking_app_plugins`); the tests skip when it is not checked out.
No plugin code is copied into this repository or executed: only the verified manifest and
declared UI document drive the host's scheduler, with the runtime transport replaced."""

import asyncio
import json
import os
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import delete

from src.api.routes import jobs as job_routes
from src.database.models.plugin_permissions import PluginPermissionGrant
from src.database.models.user import User
from src.database.session import SessionLocal
from src.features import job_runs, jobs, plugin_jobs
from src.plugin_api.publisher_trust import load_trusted_publishers
from src.plugin_api.updates import PluginPackageVerifier

ROOT = Path(os.environ.get("UTAPP_PLUGINS_REPO", "/home/user/unnamed_tracking_app_plugins"))
pytestmark = pytest.mark.skipif(
    not (ROOT / "publishers/registry.json").is_file(), reason="plugin repository unavailable"
)


def latest(plugin_id: str) -> Path:
    def key(path: Path) -> tuple[int, ...]:
        return tuple(int(part) for part in path.stem.rsplit("-", 1)[1].split("."))

    return max((ROOT / "dist").glob(f"{plugin_id}-*.utp"), key=key)


def verified(plugin_id: str):
    publishers = load_trusted_publishers(ROOT / "publishers/registry.json")
    return PluginPackageVerifier(publishers).inspect(latest(plugin_id))


def ui_document(plugin_id: str) -> dict:
    with zipfile.ZipFile(latest(plugin_id)) as archive:
        return json.loads(archive.read("payload/ui.json"))


def test_signed_epic_package_declares_a_personal_resumable_schedule():
    package = verified("official.epic-games")
    assert package.signing_version == 2
    manifest = package.manifest
    assert {c.name.value for c in manifest.capabilities} >= {"tasks.background", "games.write"}
    (task,) = manifest.scheduled_tasks
    assert task.scope == "user" and task.action_id == "sync" and task.min_interval_minutes >= 5
    assert (
        not [  # a schedule is an explicit user choice, never a default
            t for t in manifest.scheduled_tasks if getattr(t, "default_enabled", False)
        ]
    )


def test_signed_jellyfin_package_keeps_its_supervised_worker_outside_host_schedules():
    package = verified("official.jellyfin-media-sync")
    assert package.signing_version == 2
    names = {c.name.value for c in package.manifest.capabilities}
    assert "tasks.background" in names and "plugin.storage" in names
    assert not package.manifest.scheduled_tasks  # no second scheduler: the host lists no job


@pytest.fixture
async def epic(monkeypatch):
    package = verified("official.epic-games")
    installation_id, actor_id, other_id = uuid4(), uuid4(), uuid4()
    installed = {
        "plugin_id": "official.epic-games",
        "name": "Epic Games",
        "installation_id": str(installation_id),
        "api_contract_version": str(package.manifest.api_contract_version),
        "enabled": True,
        "compatible": True,
        "status": "running",
        "health": "healthy",
        "scheduled_tasks": [t.model_dump(mode="json") for t in package.manifest.scheduled_tasks],
    }
    transport = AsyncMock()
    transport.plugins.return_value = [installed]
    transport.plugin_ui.return_value = ui_document("official.epic-games")
    monkeypatch.setattr(plugin_jobs, "client", transport)
    monkeypatch.setattr(plugin_jobs, "_last_inventory", [])
    monkeypatch.setattr(plugin_jobs, "_running", {})
    async with SessionLocal() as db:
        for user_id in (actor_id, other_id):
            db.add(
                User(
                    id=user_id,
                    username="epic-" + str(user_id),
                    email=f"{user_id}@example.invalid",
                    password_hash="unused",
                )
            )
        await db.flush()
        for user_id in (actor_id,):
            for name in ("tasks.background", "games.write", "plugin.storage", "network.outbound"):
                db.add(
                    PluginPermissionGrant(
                        plugin_id="official.epic-games",
                        installation_id=installation_id,
                        user_id=user_id,
                        capability=name,
                        capability_version=1,
                    )
                )
        await db.commit()
    yield installed, transport, installation_id, actor_id, other_id
    async with SessionLocal() as db:
        await db.execute(delete(User).where(User.id.in_([actor_id, other_id])))
        await db.commit()


async def test_epic_schedule_is_listed_only_for_the_granted_account_and_starts_off(epic):
    _, _, installation_id, actor_id, other_id = epic
    job_id = plugin_jobs.task_job_id(installation_id, "autosync", actor_id)
    async with SessionLocal() as db:
        listed = await job_routes.list_user_jobs(db, await db.get(User, actor_id))
        (job,) = [task for task in listed if task["id"].startswith("user-plugin:")]
        assert job["id"] == job_id and job["available"] and not job["enabled"]
        denied = await job_routes.list_user_jobs(db, await db.get(User, other_id))
        (denied_job,) = [task for task in denied if task["id"].startswith("user-plugin:")]
        assert not denied_job["available"]
        assert await plugin_jobs.get_plugin_jobs(db) == []  # not a server task


async def test_epic_checkpoint_failure_disconnect_and_revocation_lifecycle(epic):
    installed, transport, installation_id, actor_id, _ = epic
    job_id = plugin_jobs.task_job_id(installation_id, "autosync", actor_id)
    async with SessionLocal() as db:
        actor = await db.get(User, actor_id)
        await job_routes.update_user_job(job_id, job_routes.JobUpdate(enabled=True), db, actor)
    for response, status in (
        ({"completed": False, "summary": "saved", "continue_after_seconds": 60}, "partial"),
        ({"completed": False, "summary": "Epic is busy.", "retry_after_seconds": 300}, "failed"),
        ({"completed": True, "summary": "Epic sync complete: 3 games saved."}, "completed"),
    ):
        transport.run_scheduled_task.return_value = response
        async with SessionLocal() as db:
            await job_routes.run_user_job(job_id, db, await db.get(User, actor_id))
        await asyncio.gather(*list(plugin_jobs._running.values()))
        async with SessionLocal() as db:
            row = await db.get(jobs.JobSetting, job_id)
            assert row.status == status
            assert (row.retry_at is not None) == (status == "failed")
    assert row.last_success_at and job_runs.next_run_at(row, row.last_run_at) > row.last_run_at
    transport.run_scheduled_task.return_value = {
        "completed": False,
        "summary": "Connect your Epic account first.",
        "disable_schedule": True,
    }
    async with SessionLocal() as db:
        await job_routes.run_user_job(job_id, db, await db.get(User, actor_id))
    await asyncio.gather(*list(plugin_jobs._running.values()))
    async with SessionLocal() as db:
        row = await db.get(jobs.JobSetting, job_id)
        assert not row.enabled and row.status == "disabled"
        await job_routes.update_user_job(
            job_id, job_routes.JobUpdate(enabled=True), db, await db.get(User, actor_id)
        )
        await db.execute(
            delete(PluginPermissionGrant).where(
                PluginPermissionGrant.capability == "tasks.background"
            )
        )
        await db.commit()
        await jobs.disable_revoked_schedules(db)
        assert not (await db.get(jobs.JobSetting, job_id)).enabled
