"""Public-app HTTP boundary; host verifies principals before isolated plugin execution."""

import asyncio
import json
import mimetypes
import time
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.schemas.delegation import AchievementReportWrite, TrackingWrite, integration_origin
from src.database.models.public_app import PublicAppActor, PublicAppSession, PublicAppSource
from src.database.session import get_db
from src.features import public_app_sources as sources
from src.features import public_catalogue
from src.features.delegation import integration_policy
from src.plugin_api import public_apps as apps
from src.plugin_api.outbound import public_image_request
from src.plugin_api.runtime_client import PluginRuntimeRequestError, PluginRuntimeUnavailable

_PRIVATE_HEADERS = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}


def private_response(response: Response) -> None:
    response.headers.update(_PRIVATE_HEADERS)


router = APIRouter(
    prefix="/api/public-apps", tags=["public-apps"], dependencies=[Depends(private_response)]
)
_DB = Depends(get_db)


class PublicInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BootstrapInput(PublicInput):
    proof: dict[str, Any]


class ActionInput(PublicInput):
    values: dict[str, Any]
    refresh_sources: bool = False


class SourceInput(PublicInput):
    origin: str = Field(max_length=512)

    _origin = field_validator("origin")(integration_origin)


class LegacySourceInput(SourceInput):
    api_key: str = Field(min_length=24, max_length=256, pattern=r"^utk_[A-Za-z0-9_-]+$")
    acknowledge_unrestricted_key: bool = False


class CatalogueInput(PublicInput):
    title: str = Field(min_length=1, max_length=200)
    steam_app_id: str | None = Field(default=None, pattern=r"^[0-9]{1,12}$")


@router.get("/{plugin_id}/config")
async def config(plugin_id: str, db: AsyncSession = _DB) -> dict:
    app = await apps.app_installation(db, plugin_id)
    provider = await apps.identity_provider(db, app)
    result = await apps.provider_action(provider, provider["declaration"].config_action_id, {})
    return {"plugin_id": plugin_id, "version": app["version"], "provider": result}


@router.get("/{plugin_id}/app/")
@router.get("/{plugin_id}/app/{asset_path:path}")
async def asset(plugin_id: str, asset_path: str = "", db: AsyncSession = _DB) -> Response:
    app = await apps.app_installation(db, plugin_id)
    entry = app["declaration"].entry
    path = entry if not asset_path else "frontend/" + asset_path
    if any(part in {"..", ".", ""} for part in path.split("/")) or "\\" in path:
        raise HTTPException(404, "Public-app asset not found.")
    try:
        data = await apps.client.frontend_asset(plugin_id, path)
    except (PluginRuntimeRequestError, PluginRuntimeUnavailable) as exc:
        raise HTTPException(404, "Public-app asset unavailable.") from exc
    return Response(
        data,
        media_type=mimetypes.guess_type(path)[0] or "application/octet-stream",
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'self'; script-src 'self'; "
            "style-src 'self' 'unsafe-inline'; img-src 'self' blob: data:; "
            "connect-src 'self'; object-src 'none'; base-uri 'self'; "
            "frame-ancestors 'self' https://discord.com https://*.discord.com "
            "https://discordapp.com https://*.discordapp.com",
        },
    )


@router.post("/{plugin_id}/sessions")
async def bootstrap(plugin_id: str, payload: BootstrapInput, db: AsyncSession = _DB) -> dict:
    if len(json.dumps(payload.proof)) > 8192:
        raise HTTPException(413, "Identity proof is too large.")
    return await apps.bootstrap(db, plugin_id, payload.proof)


@router.delete("/{plugin_id}/sessions", status_code=204)
async def disconnect(plugin_id: str, request: Request, db: AsyncSession = _DB) -> None:
    _app, _actor, session = await apps.authenticated(db, plugin_id, request)
    await db.delete(session)
    await db.commit()


@router.post("/{plugin_id}/action")
async def action(
    plugin_id: str, payload: ActionInput, request: Request, db: AsyncSession = _DB
) -> dict:
    if len(json.dumps(payload.values)) > 48 * 1024 or any(
        key.startswith("_") for key in payload.values
    ):
        raise HTTPException(422, "Invalid public-app action values.")
    app, actor, session = await apps.authenticated(db, plugin_id, request)
    # Check all live room contributions before exposing a cached eligibility claim.
    room_actors = select(PublicAppSession.actor_id).where(
        PublicAppSession.room == session.room,
        PublicAppSession.expires_at > int(time.time()),
    )
    room_sources = list(
        await db.scalars(
            select(PublicAppSource)
            .join(
                PublicAppActor,
                PublicAppActor.id == PublicAppSource.actor_id,
            )
            .where(
                PublicAppSource.actor_id.in_(room_actors),
                PublicAppActor.installation_id == actor.installation_id,
            )
        )
    )
    policy = await integration_policy(db)
    limit = asyncio.Semaphore(8)

    async def check(source: PublicAppSource) -> None:
        must_revalidate = (
            payload.refresh_sources
            or payload.values.get("operation") in {"roll", "finalize"}
            or (source.credential_kind == "legacy_api_key" and not policy.allow_legacy_api_keys)
        )
        if source.credential and (
            must_revalidate
            or not source.last_checked_at
            or source.last_checked_at + 5 <= int(time.time())
        ):
            async with limit:
                await sources.check_source(
                    source,
                    library=payload.refresh_sources and source.actor_id == actor.id,
                    allow_legacy=policy.allow_legacy_api_keys,
                )

    await asyncio.gather(*(check(source) for source in room_sources))
    await db.commit()
    context = await apps.actor_context(db, actor, session)
    context["source_states"] = {str(source.id): source.state for source in room_sources}
    try:
        return await apps.client.public_app_action(
            plugin_id,
            payload.values,
            installation_id=app["installation_id"],
            actor_id=str(actor.id),
            context=context,
        )
    except (PluginRuntimeRequestError, PluginRuntimeUnavailable) as exc:
        raise HTTPException(503, "The public app could not complete this action.") from exc


@router.get("/{plugin_id}/sources")
async def list_sources(plugin_id: str, request: Request, db: AsyncSession = _DB) -> list[dict]:
    _app, actor, _session = await apps.authenticated(db, plugin_id, request)
    return [
        sources.source_summary(source)
        for source in await db.scalars(
            select(PublicAppSource).where(PublicAppSource.actor_id == actor.id)
        )
    ]


@router.post("/{plugin_id}/catalogue")
async def catalogue(
    plugin_id: str,
    payload: CatalogueInput,
    request: Request,
    db: AsyncSession = _DB,
) -> dict:
    await apps.authenticated(db, plugin_id, request)
    return await public_catalogue.game_info(payload.title, payload.steam_app_id)


@router.get("/{plugin_id}/catalogue/{app_id}/artwork")
async def catalogue_artwork(
    plugin_id: str,
    app_id: str,
    request: Request,
    db: AsyncSession = _DB,
) -> Response:
    if not app_id.isdecimal() or len(app_id) > 12:
        raise HTTPException(404, "Artwork unavailable.")
    await apps.authenticated(db, plugin_id, request)
    result = await public_catalogue.artwork(app_id)
    return Response(result["content"], media_type=result["media_type"], headers=_PRIVATE_HEADERS)


@router.get("/{plugin_id}/participants/{participant_id}/avatar")
async def avatar(
    plugin_id: str,
    participant_id: UUID,
    request: Request,
    db: AsyncSession = _DB,
) -> Response:
    _app, actor, session = await apps.authenticated(db, plugin_id, request)
    participant = await db.scalar(
        select(PublicAppActor)
        .join(
            PublicAppSession,
            PublicAppSession.actor_id == PublicAppActor.id,
        )
        .where(
            PublicAppActor.id == participant_id,
            PublicAppActor.installation_id == actor.installation_id,
            PublicAppSession.room == session.room,
            PublicAppSession.expires_at > int(time.time()),
        )
        .limit(1)
    )
    if participant is None or not participant.avatar_url:
        raise HTTPException(404, "Avatar unavailable.")
    try:
        result = await asyncio.to_thread(public_image_request, participant.avatar_url)
    except (OSError, ValueError, TimeoutError) as exc:
        raise HTTPException(404, "Avatar unavailable.") from exc
    return Response(result["content"], media_type=result["media_type"], headers=_PRIVATE_HEADERS)


@router.post("/{plugin_id}/sources", status_code=201)
async def connect_source(
    plugin_id: str, payload: SourceInput, request: Request, db: AsyncSession = _DB
) -> dict:
    _app, actor, _session = await apps.authenticated(db, plugin_id, request)
    return await sources.connect_source(db, actor, integration_origin(payload.origin))


@router.post("/{plugin_id}/sources/{source_id}/poll")
async def poll_source(
    plugin_id: str, source_id: UUID, request: Request, db: AsyncSession = _DB
) -> dict:
    _app, actor, _session = await apps.authenticated(db, plugin_id, request)
    return await sources.poll_source(db, await sources.owned_source(db, actor, source_id))


@router.post("/{plugin_id}/sources/legacy", status_code=201)
async def connect_legacy_source(
    plugin_id: str,
    payload: LegacySourceInput,
    request: Request,
    db: AsyncSession = _DB,
) -> dict:
    _app, actor, _session = await apps.authenticated(db, plugin_id, request)
    return await sources.connect_legacy_source(
        db,
        actor,
        payload.origin,
        payload.api_key,
        payload.acknowledge_unrestricted_key,
    )


@router.post("/{plugin_id}/sources/{source_id}/refresh")
async def refresh_source(
    plugin_id: str, source_id: UUID, request: Request, db: AsyncSession = _DB
) -> dict:
    _app, actor, _session = await apps.authenticated(db, plugin_id, request)
    source = await sources.owned_source(db, actor, source_id)
    policy = await integration_policy(db)
    await sources.check_source(source, library=True, allow_legacy=policy.allow_legacy_api_keys)
    await db.commit()
    return sources.source_summary(source)


@router.delete("/{plugin_id}/sources/{source_id}", status_code=204)
async def delete_source(
    plugin_id: str, source_id: UUID, request: Request, db: AsyncSession = _DB
) -> None:
    _app, actor, _session = await apps.authenticated(db, plugin_id, request)
    source = await sources.owned_source(db, actor, source_id)
    await db.delete(source)
    await db.commit()


@router.get("/{plugin_id}/sources/{source_id}/games/{game_id}/artwork/{kind}")
async def artwork(
    plugin_id: str,
    source_id: UUID,
    game_id: UUID,
    kind: str,
    request: Request,
    *,
    db: AsyncSession = _DB,
) -> Response:
    if kind not in {"key_art", "banner", "logo", "icon"}:
        raise HTTPException(404, "Artwork not found.")
    _app, actor, _session = await apps.authenticated(db, plugin_id, request)
    source = await sources.owned_source(db, actor, source_id)
    result = await sources.source_request(
        source, f"/api/delegation/v1/games/{game_id}/artwork/{kind}", binary=True
    )
    return Response(result["content"], media_type="image/png", headers=_PRIVATE_HEADERS)


@router.get("/{plugin_id}/sources/{source_id}/games/{game_id}/achievements")
async def achievements(
    plugin_id: str, source_id: UUID, game_id: UUID, request: Request, db: AsyncSession = _DB
) -> dict:
    _app, actor, _session = await apps.authenticated(db, plugin_id, request)
    source = await sources.owned_source(db, actor, source_id)
    return (
        await sources.source_request(
            source, f"/api/delegation/v1/games/{game_id}/achievements?limit=200"
        )
    )["data"]


@router.patch("/{plugin_id}/sources/{source_id}/games/{game_id}/tracking")
async def tracking(
    plugin_id: str,
    source_id: UUID,
    game_id: UUID,
    payload: TrackingWrite,
    request: Request,
    *,
    db: AsyncSession = _DB,
) -> dict:
    _app, actor, _session = await apps.authenticated(db, plugin_id, request)
    source = await sources.owned_source(db, actor, source_id)
    return (
        await sources.source_request(
            source,
            f"/api/delegation/v1/games/{game_id}/tracking",
            method="PATCH",
            body=payload.model_dump(exclude_unset=True, mode="json"),
        )
    )["data"]


@router.post("/{plugin_id}/sources/{source_id}/games/{game_id}/achievement-reports")
async def achievement_report(
    plugin_id: str,
    source_id: UUID,
    game_id: UUID,
    payload: AchievementReportWrite,
    request: Request,
    *,
    db: AsyncSession = _DB,
) -> dict:
    _app, actor, _session = await apps.authenticated(db, plugin_id, request)
    source = await sources.owned_source(db, actor, source_id)
    return (
        await sources.source_request(
            source,
            f"/api/delegation/v1/games/{game_id}/achievement-reports",
            method="POST",
            body=payload.model_dump(exclude_unset=True),
        )
    )["data"]
