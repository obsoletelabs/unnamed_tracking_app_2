"""Authenticated progressive metadata sessions and plugin-owned configuration."""

from __future__ import annotations

import json
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.routes.settings import get_or_create_scan_settings
from src.api.routes.utils.games import _scan_settings_to_preferences
from src.core.auth import get_current_user
from src.core.preferences import load_preferences
from src.database.models.plugin_metadata_provider import PluginMetadataProviderRegistration
from src.database.models.user import User
from src.database.session import get_db
from src.features.metadata.handler import handler
from src.features.metadata.health import monitor
from src.features.metadata.providers import (
    configuration_presence,
    discover_providers,
    save_configuration,
)
from src.plugin_api.grants import has_capability_grant
from src.plugin_api.metadata_contracts import MediaType, MetadataCandidate, MetadataProviderRequest

router = APIRouter(prefix="/api/metadata", tags=["metadata"])
Database = Annotated[AsyncSession, Depends(get_db)]
CurrentUser = Annotated[User, Depends(get_current_user)]


class SearchInput(BaseModel):
    """A bounded query, never a caller-supplied owner or credential context."""

    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=2, max_length=100)
    media_type: MediaType = MediaType.GAME
    limit: int = Field(default=20, ge=1, le=50)


class SelectionInput(BaseModel):
    candidate_id: UUID


class FocusInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidate: MetadataCandidate


class ConfigurationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope: Literal["system", "user"] = "user"
    values: dict[str, str | None] = Field(max_length=32, repr=False)


def _owned(session_id: str, user: User):
    try:
        return handler.owned(session_id, user.id)
    except LookupError as exc:
        raise HTTPException(404, "Search session not found.") from exc


@router.post("/sessions", status_code=201)
async def start_search(payload: SearchInput, db: Database, user: CurrentUser) -> dict:
    query = payload.query.strip()
    if len(query) < 2:
        raise HTTPException(422, "Enter at least two characters to search.")
    preferences = _scan_settings_to_preferences(await get_or_create_scan_settings(user.id, db))
    providers = [
        monitor.available(provider)
        for provider in await discover_providers(db, user.id, payload.media_type, preferences)
    ]
    session = handler.start(
        MetadataProviderRequest(
            request_id=uuid4(),
            user_id=user.id,
            query=query,
            media_type=payload.media_type,
            limit=payload.limit,
            options={"steam_user_tags": (await load_preferences(db, user.id))["steam_user_tags"]},
        ),
        providers,
    )
    return session.snapshot()


@router.get("/sessions/{session_id}")
async def search_snapshot(session_id: str, user: CurrentUser) -> dict:
    return _owned(session_id, user).snapshot()


@router.post("/selection", status_code=201)
async def focus_entity(payload: FocusInput, db: Database, user: CurrentUser) -> dict:
    candidate = payload.candidate
    providers = [
        monitor.available(provider)
        for provider in await discover_providers(db, user.id, candidate.media_type)
    ]
    session = handler.focus(
        MetadataProviderRequest(
            request_id=uuid4(),
            user_id=user.id,
            query=candidate.title,
            media_type=candidate.media_type,
        ),
        providers,
        candidate,
    )
    return session.snapshot()


@router.get("/sessions/{session_id}/events")
async def search_events(
    session_id: str,
    request: Request,
    user: CurrentUser,
    after: int = Query(default=0, ge=0),
) -> StreamingResponse:
    session = _owned(session_id, user)
    last_id = request.headers.get("Last-Event-ID", "")
    if last_id.isdecimal():
        after = max(after, int(last_id))

    async def events():
        async for event in handler.stream(session, after):
            if await request.is_disconnected():
                return
            if event["event"] == "heartbeat":
                yield ": heartbeat\n\n"
            else:
                yield (f"id: {event['id']}\nevent: {event['event']}\ndata: {json.dumps(event)}\n\n")

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-store",
            "X-Accel-Buffering": "no",
        },
    )


@router.delete("/sessions/{session_id}", status_code=204)
async def cancel_search(session_id: str, user: CurrentUser) -> None:
    handler.cancel(_owned(session_id, user))


@router.post("/sessions/{session_id}/selection")
async def select_candidate(session_id: str, payload: SelectionInput, user: CurrentUser) -> dict:
    try:
        return handler.select(_owned(session_id, user), str(payload.candidate_id))
    except LookupError as exc:
        raise HTTPException(404, "Metadata candidate not found.") from exc


@router.get("/providers")
async def providers_status(db: Database, user: CurrentUser) -> dict:
    providers = await discover_providers(db, user.id)
    results = []
    for provider in providers:
        monitor.schedule(provider)
        status = monitor.status(provider)
        results.append(
            {
                **provider.declaration.model_dump(mode="json"),
                "plugin_id": provider.plugin_id,
                "state": status.state,
                "checked_at": status.checked_at or None,
                "failure": status.failure,
                "configured_fields": await configuration_presence(db, provider),
            }
        )
    return {"providers": results}


@router.put("/providers/{provider_id}/configuration")
async def update_configuration(
    provider_id: str, payload: ConfigurationInput, db: Database, user: CurrentUser
) -> dict:
    row = await db.get(PluginMetadataProviderRegistration, provider_id)
    if row is None or row.revoked_at is not None:
        raise HTTPException(404, "Metadata provider not found.")
    if payload.scope == "system" and not user.is_admin:
        raise HTTPException(403, "Administrator access is required.")
    if not await has_capability_grant(
        db,
        plugin_id=row.plugin_id,
        installation_id=row.installation_id,
        capability="metadata_providers.configuration",
        user_id=user.id,
    ):
        raise HTTPException(403, "Provider configuration permission has not been granted.")
    try:
        await save_configuration(
            db, row, "system" if payload.scope == "system" else str(user.id), payload.values
        )
    except ValueError as exc:
        raise HTTPException(422, "Provider configuration is invalid.") from exc
    for provider in await discover_providers(db, user.id):
        if provider.id == provider_id:
            monitor.schedule(provider, force=True)
    return {"saved": True, "provider_id": provider_id}
