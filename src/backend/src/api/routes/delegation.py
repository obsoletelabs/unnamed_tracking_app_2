"""User-delegated integration v1. Ordinary application APIs remain default-deny."""

import secrets
from typing import Iterable, Literal, cast
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.encoders import jsonable_encoder
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.routes.game_assets import AssetKind, get_game_asset
from src.api.routes.games import list_field_changes, update_game
from src.api.routes.media_lists import get_media_list, list_media_lists
from src.api.schemas.delegation import (
    SCOPE_DESCRIPTIONS,
    AccessDecision,
    AccessExchange,
    AccessRequest,
    AchievementReportWrite,
    ClientWrite,
    CompatibilityWrite,
    DesktopAccessRequest,
    GrantChange,
    PolicyWrite,
    TrackingWrite,
)
from src.api.schemas.game import GameUpdate
from src.core.auth import (
    AuthenticatedActor,
    effective_display_name,
    get_current_actor,
    get_current_admin,
    get_current_user,
    hash_token,
)
from src.core.config import settings
from src.core.delegation import DelegatedActor, delegated_actor
from src.database.models.anime import Anime
from src.database.models.delegation import DelegatedGrant, DelegationRequest, IntegrationClient
from src.database.models.movies import Movie
from src.database.models.public_app import PublicAppActor, PublicAppSource
from src.database.models.tv_show import TVShow
from src.database.models.user import User
from src.database.session import get_db
from src.features import delegated_library, delegation


def private_response(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


router = APIRouter(
    prefix="/api/delegation/v1",
    tags=["integration-delegation"],
    dependencies=[Depends(private_response)],
)
_DB = Depends(get_db)
_USER = Depends(get_current_user)
_ADMIN = Depends(get_current_admin)
_ACTOR = Depends(delegated_actor)
_OFFSET = Query(default=0, ge=0)
_LIMIT = Query(default=50, ge=1, le=200)


async def browser_user(
    user: User = _USER, actor: AuthenticatedActor = Depends(get_current_actor)
) -> User:
    if actor.credential_kind != "session":
        raise HTTPException(403, "Sign in through your browser to manage integration access.")
    return user


_BROWSER_USER = Depends(browser_user)


@router.get("/instance")
async def instance(db: AsyncSession = _DB) -> dict:
    policy = await delegation.integration_policy(db)
    clients = list(
        await db.scalars(select(IntegrationClient).where(IntegrationClient.enabled.is_(True)))
    )
    await db.commit()
    return {
        "protocol_version": "1.0",
        "instance_id": str(policy.instance_id),
        "enabled": policy.enabled,
        "allow_guests": policy.allow_guests,
        "allow_any_origin": policy.allow_any_origin,
        "allow_legacy_api_keys": policy.allow_legacy_api_keys,
        "allow_desktop_apps": policy.allow_desktop_apps,
        "guest_retention_hours": policy.guest_retention_hours,
        "clients": [
            {"id": str(row.id), "name": row.name, "origin": row.origin}
            for row in clients
            if row.kind == delegation.WEB
        ]
        if policy.enabled
        else [],
        # Desktop apps start consent at POST /desktop/{app}/requests.
        "desktop_apps": sorted(delegation.DESKTOP_APPS) if policy.allow_desktop_apps else [],
        "capabilities": SCOPE_DESCRIPTIONS,
    }


@router.get("/policy")
async def read_policy(_admin: User = _ADMIN, db: AsyncSession = _DB) -> dict:
    policy = await delegation.integration_policy(db)
    clients = list(
        await db.scalars(select(IntegrationClient).order_by(IntegrationClient.created_at))
    )
    await db.commit()
    return {
        "enabled": policy.enabled,
        "allow_guests": policy.allow_guests,
        "allow_any_origin": policy.allow_any_origin,
        "allow_legacy_api_keys": policy.allow_legacy_api_keys,
        "allow_desktop_apps": policy.allow_desktop_apps,
        "guest_retention_hours": policy.guest_retention_hours,
        "maximum_grant_hours": policy.maximum_grant_hours,
        "clients": [
            {
                "id": str(row.id),
                "name": row.name,
                "origin": row.origin,
                "enabled": row.enabled,
                "kind": row.kind,
            }
            for row in clients
        ],
    }


@router.put("/policy")
async def write_policy(payload: PolicyWrite, _admin: User = _ADMIN, db: AsyncSession = _DB) -> dict:
    row = await delegation.integration_policy(db)
    for field, value in payload.model_dump().items():
        setattr(row, field, value)
    await db.commit()
    return payload.model_dump()


@router.put("/clients")
async def write_client(payload: ClientWrite, _admin: User = _ADMIN, db: AsyncSession = _DB) -> dict:
    row = await db.scalar(
        select(IntegrationClient).where(IntegrationClient.origin == payload.origin)
    )
    if row is None:
        kind = delegation.DESKTOP if payload.origin.startswith("desktop:") else delegation.WEB
        row = IntegrationClient(origin=payload.origin, name=payload.name, kind=kind)
        db.add(row)
    row.name, row.enabled = payload.name, payload.enabled
    await db.commit()
    return {"id": str(row.id), **payload.model_dump()}


@router.post("/requests", status_code=201)
async def create_request(payload: AccessRequest, db: AsyncSession = _DB) -> dict:
    return await delegation.create_request(db, payload)


@router.post("/desktop/{app}/requests", status_code=201)
async def create_desktop_request(
    app: str, payload: DesktopAccessRequest, db: AsyncSession = _DB
) -> dict:
    """A first-party desktop app (such as Playnite) asks the user for access.

    The app opens `verification_path` on this instance in the user's browser and
    polls `/requests/{id}/exchange` with its verifier, exactly like a web integration."""
    return await delegation.create_desktop_request(db, app, payload)


@router.post("/clients/register", status_code=201)
async def register_client(payload: ClientWrite, db: AsyncSession = _DB) -> dict:
    return await delegation.register_client(db, payload)


@router.get("/requests/{request_id}")
async def request_summary(
    request_id: UUID, _user: User = _BROWSER_USER, db: AsyncSession = _DB
) -> dict:
    return await delegation.request_summary(db, request_id)


@router.post("/requests/{request_id}/decision")
async def decide_request(
    request_id: UUID, payload: AccessDecision, user: User = _BROWSER_USER, db: AsyncSession = _DB
) -> dict:
    return await delegation.decide_request(db, request_id, user, payload)


@router.post("/requests/{request_id}/exchange")
async def exchange_request(
    request_id: UUID, payload: AccessExchange, response: Response, db: AsyncSession = _DB
) -> dict:
    response.headers["Cache-Control"] = "no-store"
    return await delegation.exchange_request(db, request_id, payload.verifier)


@router.get("/grants")
async def grants(user: User = _BROWSER_USER, db: AsyncSession = _DB) -> list[dict]:
    rows = (
        await db.execute(
            select(DelegatedGrant, IntegrationClient, DelegationRequest.external_account)
            .join(IntegrationClient, DelegatedGrant.client_id == IntegrationClient.id)
            .join(DelegationRequest, DelegatedGrant.request_id == DelegationRequest.id)
            .where(DelegatedGrant.user_id == user.id)
            .order_by(DelegatedGrant.created_at.desc())
            .limit(200)
        )
    ).all()
    # Match an older local grant by its exact verifier-bound request, never name.
    local_links = (
        await db.execute(
            select(PublicAppSource.request_id, PublicAppActor)
            .join(PublicAppActor, PublicAppSource.actor_id == PublicAppActor.id)
            .where(
                PublicAppSource.request_id.in_([grant.request_id for grant, _, _ in rows]),
                PublicAppSource.origin == settings.PUBLIC_APP_URL.rstrip("/"),
            )
        )
    ).all()
    labels = {
        request_id: {
            "provider": actor.plugin_id,
            "subject": actor.subject,
            "username": actor.account_name or actor.display_name,
        }
        for request_id, actor in local_links
    }
    return [
        {
            "id": str(grant.id),
            "name": client.name,
            "origin": client.origin,
            "kind": client.kind,
            "enabled": client.enabled,
            "scopes": grant.scopes,
            "created_at": grant.created_at,
            "expires_at": grant.expires_at,
            "revoked_at": grant.revoked_at,
            "external_account": external_account or labels.get(grant.request_id),
        }
        for grant, client, external_account in rows
    ]


@router.patch("/grants/{grant_id}")
async def reduce_grant(
    grant_id: UUID, payload: GrantChange, user: User = _BROWSER_USER, db: AsyncSession = _DB
) -> dict:
    return await delegation.change_grant(db, user.id, grant_id, payload.scopes)


@router.delete("/grants/{grant_id}")
async def revoke_grant(grant_id: UUID, user: User = _BROWSER_USER, db: AsyncSession = _DB) -> dict:
    return await delegation.change_grant(db, user.id, grant_id, None)


@router.post("/credential/rotate")
async def rotate_credential(
    request: Request, actor: DelegatedActor = _ACTOR, db: AsyncSession = _DB
) -> dict:
    row = await delegation.owned_grant(db, actor.user.id, actor.grant.id)
    old_digest = hash_token(request.headers["authorization"][7:].strip())
    if row.token_hash is None or not secrets.compare_digest(row.token_hash, old_digest):
        raise HTTPException(401, "This integration credential has already been replaced.")
    token = delegation.TOKEN_PREFIX + secrets.token_urlsafe(48)
    row.token_hash = hash_token(token)
    delegation.record_audit(
        db, "rotated", client_id=row.client_id, user_id=row.user_id, grant_id=row.id
    )
    await db.commit()
    return {"access_token": token, "expires_at": row.expires_at, "scopes": row.scopes}


@router.delete("/credential")
async def revoke_own_credential(actor: DelegatedActor = _ACTOR, db: AsyncSession = _DB) -> dict:
    """An app signs out: its own grant is revoked, exactly as from Connected apps."""
    return await delegation.change_grant(db, actor.user.id, actor.grant.id, None)


@router.get("/credential")
async def credential_status(actor: DelegatedActor = _ACTOR) -> dict:
    return {"scopes": actor.grant.scopes, "expires_at": actor.grant.expires_at}


@router.get("/profile")
async def profile(actor: DelegatedActor = _ACTOR) -> dict:
    actor.require("profile.read")
    return {"id": str(actor.user.id), "display_name": effective_display_name(actor.user)}


@router.get("/games")
async def games(
    actor: DelegatedActor = _ACTOR,
    db: AsyncSession = _DB,
    offset: int = _OFFSET,
    limit: int = _LIMIT,
) -> dict:
    actor.require("games.read")
    return await delegated_library.library_page(db, actor.user.id, offset, limit)


@router.get("/games/{game_id}")
async def game(game_id: UUID, actor: DelegatedActor = _ACTOR, db: AsyncSession = _DB) -> dict:
    actor.require("games.read")
    return delegated_library.library_game(
        await delegated_library.delegated_game(db, actor.user.id, game_id)
    )


@router.patch("/games/{game_id}/tracking")
async def tracking(
    game_id: UUID, payload: TrackingWrite, actor: DelegatedActor = _ACTOR, db: AsyncSession = _DB
) -> dict:
    actor.require("tracking.write")
    row = await update_game(
        game_id,
        GameUpdate(**payload.model_dump(exclude_unset=True)),
        db,
        actor.user,
        AuthenticatedActor(actor.user.id, "api_key"),
    )
    delegation.record_audit(
        db,
        "tracking_updated",
        client_id=actor.grant.client_id,
        user_id=actor.user.id,
        grant_id=actor.grant.id,
    )
    await db.commit()
    return {"id": str(row.id), **jsonable_encoder(payload.model_dump(exclude_unset=True))}


@router.patch("/games/{game_id}/play-compatibility")
async def play_compatibility(
    game_id: UUID,
    payload: CompatibilityWrite,
    actor: DelegatedActor = _ACTOR,
    db: AsyncSession = _DB,
) -> dict:
    actor.require("compatibility.write")
    row = await update_game(
        game_id,
        GameUpdate(**payload.model_dump()),
        db,
        actor.user,
        AuthenticatedActor(actor.user.id, "api_key"),
    )
    delegation.record_audit(
        db,
        "compatibility_updated",
        client_id=actor.grant.client_id,
        user_id=actor.user.id,
        grant_id=actor.grant.id,
    )
    await db.commit()
    return {"id": str(row.id), **jsonable_encoder(payload.model_dump())}


@router.get("/games/{game_id}/achievements")
async def achievements(
    game_id: UUID,
    actor: DelegatedActor = _ACTOR,
    db: AsyncSession = _DB,
    offset: int = _OFFSET,
    limit: int = _LIMIT,
) -> dict:
    actor.require("achievements.read")
    return await delegated_library.achievement_page(db, actor.user.id, game_id, offset, limit)


@router.post("/games/{game_id}/achievement-reports")
async def report_achievement(
    game_id: UUID,
    payload: AchievementReportWrite,
    actor: DelegatedActor = _ACTOR,
    db: AsyncSession = _DB,
) -> dict:
    actor.require("achievements.write")
    result = await delegated_library.write_achievement_report(db, actor.user.id, game_id, payload)
    delegation.record_audit(
        db,
        "achievement_reported",
        client_id=actor.grant.client_id,
        user_id=actor.user.id,
        grant_id=actor.grant.id,
    )
    await db.commit()
    return result


@router.delete("/games/{game_id}/achievement-reports/{report_id}", status_code=204)
async def clear_achievement_report(
    game_id: UUID, report_id: UUID, actor: DelegatedActor = _ACTOR, db: AsyncSession = _DB
) -> None:
    actor.require("achievements.write")
    await delegated_library.delete_achievement_report(db, actor.user.id, game_id, report_id)
    delegation.record_audit(
        db,
        "achievement_cleared",
        client_id=actor.grant.client_id,
        user_id=actor.user.id,
        grant_id=actor.grant.id,
    )
    await db.commit()


@router.get("/games/{game_id}/history")
async def history(game_id: UUID, actor: DelegatedActor = _ACTOR, db: AsyncSession = _DB) -> list:
    actor.require("history.read")
    return await list_field_changes(game_id, db, actor.user)


@router.get("/games/{game_id}/artwork/{kind}")
async def artwork(
    request: Request,
    game_id: UUID,
    kind: AssetKind,
    actor: DelegatedActor = _ACTOR,
    db: AsyncSession = _DB,
) -> Response:
    actor.require("games.read")
    result = await get_game_asset(
        request, game_id=game_id, asset_kind=kind, db=db, current_user=actor.user
    )
    result.headers["Cache-Control"] = "no-store"
    return result


@router.get("/lists")
async def lists(actor: DelegatedActor = _ACTOR, db: AsyncSession = _DB) -> list:
    actor.require("lists.read")
    return await list_media_lists(db, actor.user)


@router.get("/lists/{list_id}")
async def custom_list(
    list_id: UUID, actor: DelegatedActor = _ACTOR, db: AsyncSession = _DB
) -> dict:
    actor.require("lists.read")
    return await get_media_list(list_id, db, actor.user)


@router.get("/media")
async def tracked_media(
    kind: Literal["movie", "tv", "anime"] = "movie",
    actor: DelegatedActor = _ACTOR,
    db: AsyncSession = _DB,
    offset: int = _OFFSET,
    limit: int = _LIMIT,
) -> dict:
    actor.require("media.read")
    models: dict[str, type[Movie] | type[TVShow] | type[Anime]] = {
        "movie": Movie,
        "tv": TVShow,
        "anime": Anime,
    }
    model = models[kind]
    rows = list(
        cast(
            Iterable[Movie | TVShow | Anime],
            await db.scalars(
                select(model)
                .where(
                    model.user_id == actor.user.id,
                    model.deleted_at.is_(None),
                )
                .order_by(model.id)
                .offset(offset)
                .limit(limit + 1)
            ),
        )
    )
    return {
        "items": [
            jsonable_encoder(
                {
                    "id": row.id,
                    "title": row.title,
                    "kind": kind,
                    "status": row.status,
                    "updated_at": row.updated_at,
                }
            )
            for row in rows[:limit]
        ],
        "next_offset": offset + min(limit, len(rows)),
        "complete": len(rows) <= limit,
    }
