"""Server-side Connections page state and the host-run plugin sync."""

import asyncio
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import delete
from starlette.requests import Request

from src.api.routes import connections as routes
from src.api.routes.plugin_manager import runtime
from src.database.models.job_setting import JobSetting
from src.database.models.plugin_permissions import PluginPermissionGrant
from src.database.models.user import User
from src.database.session import SessionLocal
from src.features import plugin_jobs

CAPABILITIES = ("frontend.connections", "plugin.storage", "games.write", "tasks.background")
VERSION = "1.1.11"


def _request() -> Request:
    return Request({"type": "http", "headers": [], "method": "GET", "path": "/"})


def _document(**connection):
    return {
        "schema_version": "v1",
        "api_contract_version": VERSION,
        "plugin_id": "example.library",
        "title": "Library",
        "settings": [],
        "tables": [],
        "dialogs": [],
        "menus": [],
        "actions": [
            {
                "id": "status",
                "label": "Status",
                "handler": "plugin:status",
                "capability": {"name": "plugin.storage", "version": 1},
            },
            {
                "id": "sync",
                "label": "Sync",
                "handler": "plugin:sync",
                "capability": {"name": "games.write", "version": 1},
            },
        ],
        "pages": [
            {
                "id": "library",
                "title": "Library",
                "description": "",
                "settings": [],
                "actions": ["status", "sync"],
                "tables": [],
                "dialogs": [],
            }
        ],
        "connections": [
            {
                "id": "account",
                "label": "Example",
                "description": "Import a library.",
                "page_id": "library",
                "status_action_id": "status",
                "sync_action_id": "sync",
                **connection,
            }
        ],
    }


@pytest.fixture
async def library(monkeypatch):
    """Real grants and job rows; only the isolated runtime transport is replaced."""
    installation_id, user_id = uuid4(), uuid4()
    installed = {
        "plugin_id": "example.library",
        "name": "Example library",
        "installation_id": str(installation_id),
        "api_contract_version": VERSION,
        "enabled": True,
        "compatible": True,
        "status": "running",
        "health": "healthy",
        "scheduled_tasks": [
            {"id": "autosync", "name": "Autosync", "action_id": "sync", "scope": "user"}
        ],
    }
    transport = AsyncMock()
    transport.plugins.return_value = [installed]
    transport.plugin_ui.return_value = _document()
    transport.action.return_value = {"connected": True, "display_name": "  Ada  "}
    transport.run_scheduled_task.return_value = {"completed": True, "summary": "3 games saved"}
    monkeypatch.setattr(plugin_jobs, "client", transport)
    monkeypatch.setattr(plugin_jobs, "_last_inventory", [])
    monkeypatch.setattr(plugin_jobs, "_running", {})
    monkeypatch.setattr(runtime, "client", transport)
    monkeypatch.setattr(runtime, "installed_plugins", AsyncMock(return_value=[installed]))
    async with SessionLocal() as db:
        db.add(
            User(
                id=user_id,
                username="conn-" + str(user_id),
                email=f"{user_id}@example.invalid",
                password_hash="unused",
            )
        )
        await db.flush()
        for capability in CAPABILITIES:
            db.add(
                PluginPermissionGrant(
                    plugin_id="example.library",
                    installation_id=installation_id,
                    user_id=user_id,
                    capability=capability,
                    capability_version=1,
                )
            )
        await db.commit()
    try:
        yield installed, transport, installation_id, user_id
    finally:
        running = list(plugin_jobs._running.values())
        for task_run in running:
            task_run.cancel()
        await asyncio.gather(*running, return_exceptions=True)
        async with SessionLocal() as db:
            await db.execute(
                delete(JobSetting).where(
                    JobSetting.job_id
                    == plugin_jobs.task_job_id(installation_id, "autosync", user_id)
                )
            )
            await db.execute(
                delete(PluginPermissionGrant).where(PluginPermissionGrant.user_id == user_id)
            )
            await db.execute(delete(User).where(User.id == user_id))
            await db.commit()


async def _load(user_id):
    async with SessionLocal() as db:
        user = await db.get(User, user_id)
        return await routes.plugin_connections(_request(), db, user)


async def _start(user_id):
    async with SessionLocal() as db:
        user = await db.get(User, user_id)
        return await routes.sync_plugin_connection("example.library", "account", db, user)


@pytest.mark.asyncio
async def test_status_and_sync_state_are_resolved_by_the_host(library):
    _, transport, _, user_id = library
    body = await _load(user_id)
    assert body["connections"] == [
        {
            "plugin_id": "example.library",
            "connection_id": "account",
            "connected": True,
            "display_name": "Ada",
            "error": False,
            "sync": {
                "available": True,
                "unavailable_reason": "",
                "running": False,
                "paused": False,
                "status": "idle",
                "last_run_at": None,
                "last_summary": "",
            },
        }
    ]
    assert transport.action.await_args.args[:2] == ("example.library", "status")


@pytest.mark.asyncio
async def test_a_failing_or_malformed_status_is_an_error_not_a_connection(library):
    _, transport, _, user_id = library
    transport.action.return_value = {"ok": False, "error": "x"}
    assert (await _load(user_id))["connections"][0]["error"] is True
    transport.action.return_value = {"connected": "yes", "display_name": 5}
    row = (await _load(user_id))["connections"][0]
    assert row["connected"] is False and row["display_name"] is None and row["error"] is False
    transport.action.side_effect = TimeoutError()
    row = (await _load(user_id))["connections"][0]
    assert row["connected"] is False and row["error"] is True


@pytest.mark.asyncio
async def test_connections_need_the_frontend_grant(library):
    _, _, _, user_id = library
    async with SessionLocal() as db:
        await db.execute(
            delete(PluginPermissionGrant).where(
                PluginPermissionGrant.user_id == user_id,
                PluginPermissionGrant.capability == "frontend.connections",
            )
        )
        await db.commit()
    assert (await _load(user_id))["connections"] == []


@pytest.mark.asyncio
async def test_sync_runs_every_checkpoint_on_the_server_until_complete(library):
    _, transport, installation_id, user_id = library
    transport.run_scheduled_task.side_effect = [
        {"completed": False, "summary": "inventory saved", "continue_after_seconds": 60},
        {"completed": False, "summary": "catalogue saved", "continue_after_seconds": 60},
        {"completed": True, "summary": "3 games saved"},
    ]
    started = await _start(user_id)
    assert started["sync"]["running"] is True
    await asyncio.gather(*list(plugin_jobs._running.values()))
    assert transport.run_scheduled_task.await_count == 3
    sync = (await _load(user_id))["connections"][0]["sync"]
    assert sync["running"] is False and sync["status"] == "completed"
    assert sync["last_summary"] == "3 games saved" and sync["last_run_at"]


@pytest.mark.asyncio
async def test_a_rate_limit_stops_the_run_and_is_reported(library):
    _, transport, _, user_id = library
    transport.run_scheduled_task.return_value = {
        "completed": False,
        "summary": "Epic is rate limiting requests.",
        "retry_after_seconds": 300,
    }
    await _start(user_id)
    await asyncio.gather(*list(plugin_jobs._running.values()))
    transport.run_scheduled_task.assert_awaited_once()
    sync = (await _load(user_id))["connections"][0]["sync"]
    assert sync["status"] == "failed" and sync["last_summary"].startswith("Epic is rate")


@pytest.mark.asyncio
async def test_sync_is_bounded_to_a_fixed_number_of_checkpoints(library):
    _, transport, _, user_id = library
    transport.run_scheduled_task.return_value = {
        "completed": False,
        "summary": "saved",
        "continue_after_seconds": 60,
    }
    await _start(user_id)
    await asyncio.gather(*list(plugin_jobs._running.values()))
    assert transport.run_scheduled_task.await_count == plugin_jobs.MAX_SYNC_STEPS


@pytest.mark.asyncio
async def test_sync_is_refused_without_background_access_or_a_declared_sync(library):
    installed, transport, _, user_id = library
    async with SessionLocal() as db:
        await db.execute(
            delete(PluginPermissionGrant).where(
                PluginPermissionGrant.user_id == user_id,
                PluginPermissionGrant.capability == "tasks.background",
            )
        )
        await db.commit()
    sync = (await _load(user_id))["connections"][0]["sync"]
    assert sync["available"] is False and sync["unavailable_reason"]
    with pytest.raises(HTTPException) as refused:
        await _start(user_id)
    assert refused.value.status_code == 409
    transport.run_scheduled_task.assert_not_awaited()

    installed["scheduled_tasks"] = []
    assert (await _load(user_id))["connections"][0]["sync"] is None
    with pytest.raises(HTTPException) as missing:
        await _start(user_id)
    assert missing.value.status_code == 404


@pytest.mark.asyncio
async def test_unknown_connections_are_not_found(library):
    _, _, _, user_id = library
    async with SessionLocal() as db:
        user = await db.get(User, user_id)
        with pytest.raises(HTTPException) as missing:
            await routes.sync_plugin_connection("example.library", "other", db, user)
    assert missing.value.status_code == 404
