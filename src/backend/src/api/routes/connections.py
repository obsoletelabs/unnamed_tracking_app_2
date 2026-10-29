"""Connections page state, loaded and synced on the server.

The page used to ask each plugin for its status from the browser and ran a plugin sync by
driving the plugin's own page. Here the host calls the declared status action, resolves the
account's personal sync task and starts it itself, so the browser only renders what it is
told and polls for progress."""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.routes.plugin_manager import models, runtime
from src.api.routes.plugin_manager.contributions import (
    _authorize_action,
    _host_action_context,
    _validated_ui_document,
)
from src.core.auth import get_current_user
from src.database.models.user import User
from src.database.session import get_db
from src.features.jobs import JobSpec, describe, get_setting
from src.features.plugin_jobs import personal_sync_job
from src.plugin_api.grants import installation_is_executable
from src.plugin_api.lifecycle import plugin_contract_active
from src.plugin_api.runtime_client import PluginRuntimeRequestError, PluginRuntimeUnavailable
from src.plugin_api.ui_contracts import UiConnectionContribution

logger = logging.getLogger(__name__)
router = APIRouter(
    prefix="/api/connections",
    tags=["connections"],
    dependencies=[Depends(runtime.private_plugin_response)],
)
_DB = Depends(get_db)
_USER = Depends(get_current_user)
STATUS_TIMEOUT_SECONDS = 8.0
MAX_DISPLAY_NAME = 80


def _status_view(result: dict[str, Any] | None) -> dict[str, Any]:
    """Accept only a boolean and a short plain-text name from the untrusted plugin."""
    if result is None or result.get("ok") is False:
        return {"connected": False, "display_name": None, "error": True}
    name = result.get("display_name")
    name = name.strip()[:MAX_DISPLAY_NAME] if isinstance(name, str) else ""
    return {
        "connected": result.get("connected") is True,
        "display_name": name or None,
        "error": False,
    }


async def _status_call(
    request: Request,
    db: AsyncSession,
    user: User,
    entry: tuple[dict[str, Any], Any, UiConnectionContribution],
) -> tuple[str, str, dict[str, Any]] | None:
    """Authorize the declared status action as the signed-in user; None when it cannot run."""
    plugin, document, contribution = entry
    plugin_id, action_id = plugin["plugin_id"], contribution.status_action_id
    if action_id is None:
        return None
    payload = models.PluginActionIn(values={})
    try:
        _, installation_id = await _authorize_action(
            plugin_id, action_id, payload, db=db, plugin=plugin, user=user
        )
        context = await _host_action_context(
            plugin_id,
            action_id,
            payload,
            db=db,
            document=document.model_dump(mode="json"),
            installation_id=installation_id,
            user=user,
            request=request,
        )
    except HTTPException:
        logger.warning("Plugin connection status not permitted: plugin_id=%s", plugin_id)
        return None
    return plugin_id, action_id, {"_plugin_context": context}


async def _run_status(
    user: User, call: tuple[str, str, dict[str, Any]] | None, declared: bool
) -> dict[str, Any]:
    """Run an authorized status action; a plugin that declares none is simply not connected."""
    if not declared:
        return {"connected": False, "display_name": None, "error": False}
    if call is None:
        return _status_view(None)
    plugin_id, action_id, values = call
    try:
        result = await asyncio.wait_for(
            runtime.client.action(
                quote(plugin_id, safe=""), quote(action_id, safe=""), values, user_id=str(user.id)
            ),
            STATUS_TIMEOUT_SECONDS,
        )
    except (PluginRuntimeRequestError, PluginRuntimeUnavailable, TimeoutError):
        logger.warning("Plugin connection status unavailable: plugin_id=%s", plugin_id)
        return _status_view(None)
    return _status_view(result)


async def _sync_view(db: AsyncSession, spec: JobSpec | None) -> dict[str, Any] | None:
    if spec is None:
        return None
    job = await describe(db, spec)
    return {
        "available": job["available"],
        "unavailable_reason": job["unavailable_reason"],
        "running": job["running"],
        "paused": job["paused"],
        "status": job["status"],
        "last_run_at": job["last_run_at"],
        "last_summary": job["last_summary"],
    }


async def _connection_documents(
    db: AsyncSession, user: User
) -> list[tuple[dict[str, Any], Any, UiConnectionContribution]]:
    """Each plugin connection this account may see: installed, running and granted."""
    found: list[tuple[dict[str, Any], Any, UiConnectionContribution]] = []
    for plugin in await runtime.installed_plugins():
        installation_id = plugin.get("installation_id")
        if not installation_id or not installation_is_executable(plugin):
            continue
        if not plugin_contract_active(plugin):
            continue
        granted = await runtime.active_user_capabilities(
            db, str(plugin["plugin_id"]), UUID(str(installation_id)), user.id
        )
        capabilities = await runtime.effective_capabilities(
            db, str(plugin["plugin_id"]), UUID(str(installation_id)), user.id, granted
        )
        if "frontend.connections" not in capabilities:
            continue
        try:
            document = await _validated_ui_document(str(plugin["plugin_id"]), plugin)
        except HTTPException:
            continue
        pages = {page.id for page in document.pages}
        found.extend(
            (plugin, document, item) for item in document.connections if item.page_id in pages
        )
    return found


@router.get("/plugins")
async def plugin_connections(
    request: Request, db: AsyncSession = _DB, user: User = _USER
) -> dict[str, Any]:
    """Status and sync state of every plugin connection, computed by the host."""
    entries = await _connection_documents(db, user)
    # Authorization shares one database session, so it is sequential; the plugins themselves
    # are then asked at the same time.
    calls = [await _status_call(request, db, user, entry) for entry in entries]
    statuses = await asyncio.gather(
        *(
            _run_status(user, call, entry[2].status_action_id is not None)
            for call, entry in zip(calls, entries, strict=True)
        )
    )
    items = []
    for (plugin, _, item), status in zip(entries, statuses, strict=True):
        spec = (
            await personal_sync_job(db, plugin, user.id, item.sync_action_id)
            if item.sync_action_id
            else None
        )
        items.append(
            {
                "plugin_id": plugin["plugin_id"],
                "connection_id": item.id,
                **status,
                "sync": await _sync_view(db, spec),
            }
        )
    await db.commit()
    return {"connections": items}


@router.post("/plugins/{plugin_id}/{connection_id}/sync")
async def sync_plugin_connection(
    plugin_id: str, connection_id: str, db: AsyncSession = _DB, user: User = _USER
) -> dict[str, Any]:
    """Start this account's sync for one connection and return at once; progress is polled."""
    entry = next(
        (
            found
            for found in await _connection_documents(db, user)
            if found[0]["plugin_id"] == plugin_id and found[2].id == connection_id
        ),
        None,
    )
    if entry is None:
        raise HTTPException(status_code=404, detail="No such connection.")
    plugin, _, item = entry
    spec = (
        await personal_sync_job(db, plugin, user.id, item.sync_action_id)
        if item.sync_action_id
        else None
    )
    if spec is None:
        raise HTTPException(status_code=404, detail="This connection has no sync.")
    if not spec.available:
        raise HTTPException(status_code=409, detail=spec.unavailable_reason)
    if (await get_setting(db, spec)).paused:
        raise HTTPException(status_code=409, detail="This task is paused. Resume it first.")
    await db.commit()
    spec.start("complete")
    return {"sync": await _sync_view(db, spec)}
