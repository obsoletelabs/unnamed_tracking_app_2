"""One-use, outbound-only Playnite launch delivery. No host-side command execution."""

import time
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.functions import count

from src.api.schemas.remote_launch import LaunchCreate, LaunchResult
from src.core.delegation import DelegatedActor
from src.database.models.delegation import (
    DelegatedGrant,
    DelegationAudit,
    DelegationRequest,
    IntegrationClient,
)
from src.database.models.game import Game, GameStatus
from src.database.models.remote_launch import RemoteLaunchDevice, RemoteLaunchRequest
from src.database.models.user import User
from src.features import delegation

SCOPE = "games.launch"
REQUEST_TTL_SECONDS = 60
POLL_INTERVAL_SECONDS = 5
ONLINE_SECONDS = 20
HISTORY_SECONDS = 7 * 86400
ACTIVE_STATES = ("pending", "dispatched")


def contract_description() -> dict:
    return {
        "contract": "playnite-remote-launch",
        "version": 1,
        "scope": SCOPE,
        "request_ttl_seconds": REQUEST_TTL_SECONDS,
        "poll_interval_seconds": POLL_INTERVAL_SECONDS,
        "online_seconds": ONLINE_SECONDS,
        "delivery": "at-most-once",
    }


def _has_access(grant: DelegatedGrant) -> bool:
    return (
        grant.revoked_at is None
        and grant.token_hash is not None
        and (grant.expires_at is None or grant.expires_at > int(time.time()))
        and SCOPE in grant.scopes
    )


async def _grant(db: AsyncSession, user_id: UUID, grant_id: UUID) -> DelegatedGrant:
    grant = await delegation.owned_grant(db, user_id, grant_id)
    client = await delegation.enabled_client(db, grant.client_id)
    if client.kind != delegation.DESKTOP or client.origin != "desktop:playnite":
        raise HTTPException(403, "Remote launch requires an approved Playnite desktop connection.")
    if not _has_access(grant):
        raise HTTPException(403, "This computer does not have remote launch permission.")
    return grant


async def _receiver(db: AsyncSession, actor: DelegatedActor) -> DelegatedGrant:
    actor.require(SCOPE)
    return await _grant(db, actor.user.id, actor.grant.id)


def _audit(db: AsyncSession, grant: DelegatedGrant, state: str) -> None:
    delegation.record_audit(
        db,
        "launch." + state,
        client_id=grant.client_id,
        user_id=grant.user_id,
        grant_id=grant.id,
    )


async def _device(db: AsyncSession, grant_id: UUID) -> RemoteLaunchDevice:
    row = await db.get(RemoteLaunchDevice, grant_id, with_for_update=True, populate_existing=True)
    if row is None:
        raise HTTPException(409, "Enable remote launch in Playnite on this computer first.")
    return row


def _finish(
    db: AsyncSession,
    grant: DelegatedGrant,
    row: RemoteLaunchRequest,
    state: str,
    failure_code: str | None = None,
) -> None:
    row.state = state
    row.failure_code = failure_code
    row.completed_at = int(time.time())
    _audit(db, grant, state)


def _expire(db: AsyncSession, grant: DelegatedGrant, row: RemoteLaunchRequest) -> None:
    if row.state in ACTIVE_STATES and row.expires_at <= int(time.time()):
        _finish(db, grant, row, "unknown" if row.state == "dispatched" else "expired")


def request_view(row: RemoteLaunchRequest) -> dict:
    return {
        "id": str(row.id),
        "device_id": str(row.device_id),
        "game_id": str(row.game_id),
        "state": row.state,
        "failure_code": row.failure_code,
        "created_at": row.created_at,
        "expires_at": row.expires_at,
        "dispatched_at": row.dispatched_at,
        "completed_at": row.completed_at,
    }


async def configure_device(db: AsyncSession, actor: DelegatedActor, enabled: bool) -> dict:
    grant = await _receiver(db, actor)
    row = await db.get(RemoteLaunchDevice, grant.id, with_for_update=True, populate_existing=True)
    if row is None:
        row = RemoteLaunchDevice(grant_id=grant.id, enabled=False, last_seen_at=int(time.time()))
        db.add(row)
    if row.enabled != enabled:
        _audit(db, grant, "enabled" if enabled else "disabled")
    row.enabled = enabled
    row.last_seen_at = int(time.time())
    if not enabled:
        requests = await db.scalars(
            select(RemoteLaunchRequest)
            .where(
                RemoteLaunchRequest.device_id == grant.id,
                RemoteLaunchRequest.state.in_(ACTIVE_STATES),
            )
            .with_for_update()
        )
        for request in requests:
            _finish(db, grant, request, "unknown" if request.state == "dispatched" else "expired")
    await db.commit()
    return {"id": str(row.grant_id), "enabled": row.enabled}


async def list_devices(db: AsyncSession, user_id: UUID) -> list[dict]:
    policy = await delegation.integration_policy(db)
    if not policy.allow_desktop_apps:
        return []
    now = int(time.time())
    rows = await db.execute(
        select(RemoteLaunchDevice, DelegationRequest.external_account)
        .join(DelegatedGrant, RemoteLaunchDevice.grant_id == DelegatedGrant.id)
        .join(IntegrationClient, DelegatedGrant.client_id == IntegrationClient.id)
        .join(DelegationRequest, DelegatedGrant.request_id == DelegationRequest.id)
        .where(
            DelegatedGrant.user_id == user_id,
            DelegatedGrant.revoked_at.is_(None),
            DelegatedGrant.token_hash.is_not(None),
            or_(DelegatedGrant.expires_at.is_(None), DelegatedGrant.expires_at > now),
            DelegatedGrant.scopes.contains([SCOPE]),
            IntegrationClient.kind == delegation.DESKTOP,
            IntegrationClient.origin == "desktop:playnite",
            IntegrationClient.enabled.is_(True),
            RemoteLaunchDevice.enabled.is_(True),
        )
        .order_by(RemoteLaunchDevice.grant_id)
        .limit(200)
    )
    return [
        {
            "id": str(device.grant_id),
            "name": (label or {}).get("username", "Playnite computer"),
            "last_seen_at": device.last_seen_at,
            "online": device.last_seen_at > now - ONLINE_SECONDS,
        }
        for device, label in rows
    ]


async def _owned_request(
    db: AsyncSession,
    user_id: UUID,
    request_id: UUID,
) -> RemoteLaunchRequest:
    row = await db.scalar(
        select(RemoteLaunchRequest)
        .where(
            RemoteLaunchRequest.id == request_id,
            RemoteLaunchRequest.user_id == user_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None:
        raise HTTPException(404, "Launch request not found.")
    return row


async def enqueue(db: AsyncSession, user_id: UUID, payload: LaunchCreate) -> dict:
    # Serialize this user's idempotency and rate checks across workers and target computers.
    await db.scalar(select(User.id).where(User.id == user_id).with_for_update())
    existing = await db.scalar(
        select(RemoteLaunchRequest).where(
            RemoteLaunchRequest.user_id == user_id,
            RemoteLaunchRequest.request_key == payload.request_key,
        )
    )
    if existing is not None:
        if existing.device_id != payload.device_id or existing.game_id != payload.game_id:
            raise HTTPException(409, "This request key was already used for a different launch.")
        return await read_request(db, user_id, existing.id)
    grant = await _grant(db, user_id, payload.device_id)
    device = await _device(db, grant.id)
    now = int(time.time())
    if not device.enabled or device.last_seen_at <= now - ONLINE_SECONDS:
        raise HTTPException(409, "This computer is offline or remote launch is disabled.")
    game = await db.scalar(
        select(Game)
        .where(
            Game.id == payload.game_id,
            Game.user_id == user_id,
            Game.deleted_at.is_(None),
        )
        .with_for_update()
    )
    if game is None:
        raise HTTPException(404, "Game not found.")
    if game.status == GameStatus.WISHLIST:
        raise HTTPException(409, "A wishlisted game cannot be launched remotely.")
    if game.playnite_guid is None:
        raise HTTPException(409, "This game is not linked to Playnite.")
    active = await db.scalars(
        select(RemoteLaunchRequest)
        .where(
            RemoteLaunchRequest.device_id == device.grant_id,
            RemoteLaunchRequest.state.in_(ACTIVE_STATES),
        )
        .with_for_update()
    )
    for row in active:
        _expire(db, grant, row)
        if row.state in ACTIVE_STATES:
            raise HTTPException(409, "This computer already has an outstanding launch request.")
    recent = await db.scalar(
        select(count())
        .select_from(RemoteLaunchRequest)
        .where(
            RemoteLaunchRequest.user_id == user_id,
            RemoteLaunchRequest.created_at > now - 60,
        )
    )
    hourly = await db.scalar(
        select(count())
        .select_from(RemoteLaunchRequest)
        .where(
            RemoteLaunchRequest.user_id == user_id,
            RemoteLaunchRequest.created_at > now - 3600,
        )
    )
    if (recent or 0) >= 6 or (hourly or 0) >= 60:
        raise HTTPException(
            429, "Too many launch requests. Try again later.", headers={"Retry-After": "60"}
        )
    row = RemoteLaunchRequest(
        user_id=user_id,
        device_id=device.grant_id,
        game_id=game.id,
        playnite_id=game.playnite_guid,
        request_key=payload.request_key,
        created_at=now,
        expires_at=now + REQUEST_TTL_SECONDS,
    )
    db.add(row)
    _audit(db, grant, "queued")
    await db.commit()
    return request_view(row)


async def read_request(db: AsyncSession, user_id: UUID, request_id: UUID) -> dict:
    # Always lock grant -> device -> request, matching claim/disable/result operations.
    device_id = await db.scalar(
        select(RemoteLaunchRequest.device_id).where(
            RemoteLaunchRequest.id == request_id,
            RemoteLaunchRequest.user_id == user_id,
        )
    )
    if device_id is None:
        raise HTTPException(404, "Launch request not found.")
    grant = await delegation.owned_grant(db, user_id, device_id)
    device = await _device(db, grant.id)
    row = await _owned_request(db, user_id, request_id)
    _expire(db, grant, row)
    client = await db.get(IntegrationClient, grant.client_id)
    policy = await delegation.integration_policy(db)
    if row.state in ACTIVE_STATES and (
        not device.enabled
        or not _has_access(grant)
        or client is None
        or not delegation.client_allowed(policy, client)
    ):
        _finish(db, grant, row, "unknown" if row.state == "dispatched" else "expired")
    await db.commit()
    return request_view(row)


async def claim(db: AsyncSession, actor: DelegatedActor) -> dict:
    grant = await _receiver(db, actor)
    device = await _device(db, grant.id)
    if not device.enabled:
        raise HTTPException(409, "Remote launch is disabled on this computer.")
    device.last_seen_at = int(time.time())
    rows = await db.scalars(
        select(RemoteLaunchRequest)
        .where(
            RemoteLaunchRequest.device_id == grant.id,
            RemoteLaunchRequest.state.in_(ACTIVE_STATES),
        )
        .order_by(RemoteLaunchRequest.created_at, RemoteLaunchRequest.id)
    )
    result = None
    for candidate in rows:
        # Game deletion takes the game lock before its request cascade. Match that order.
        game = None
        if candidate.state == "pending":
            game = await db.get(
                Game, candidate.game_id, with_for_update=True, populate_existing=True
            )
        row = await db.get(
            RemoteLaunchRequest,
            candidate.id,
            with_for_update=True,
            populate_existing=True,
        )
        if row is None:
            continue
        _expire(db, grant, row)
        if row.state != "pending":
            continue
        if (
            game is None
            or game.user_id != actor.user.id
            or game.deleted_at is not None
            or game.status == GameStatus.WISHLIST
            or game.playnite_guid != row.playnite_id
        ):
            _finish(db, grant, row, "failed", "game_unavailable")
            continue
        # Commit before returning: a lost response must never cause automatic redelivery.
        row.state = "dispatched"
        row.dispatched_at = int(time.time())
        _audit(db, grant, "dispatched")
        result = {
            "id": str(row.id),
            "playnite_id": str(row.playnite_id),
            "expires_at": row.expires_at,
        }
        break
    await db.commit()
    return {
        "request": result,
        "poll_interval_seconds": POLL_INTERVAL_SECONDS,
        "server_time": int(time.time()),
    }


async def report_result(
    db: AsyncSession,
    actor: DelegatedActor,
    request_id: UUID,
    payload: LaunchResult,
) -> dict:
    grant = await _receiver(db, actor)
    await _device(db, grant.id)
    row = await _owned_request(db, actor.user.id, request_id)
    if row.device_id != grant.id:
        raise HTTPException(404, "Launch request not found.")
    _expire(db, grant, row)
    if row.state == payload.state and row.failure_code == payload.failure_code:
        return request_view(row)
    if row.state != "dispatched":
        await db.commit()
        raise HTTPException(409, "This launch request no longer accepts a result.")
    _finish(db, grant, row, payload.state, payload.failure_code)
    await db.commit()
    return request_view(row)


async def purge_history(db: AsyncSession) -> int:
    """Bound each maintenance pass; idle accounts do not retain launch history forever."""
    cutoff = int(time.time()) - HISTORY_SECONDS
    ids = select(RemoteLaunchRequest.id).where(RemoteLaunchRequest.expires_at < cutoff).limit(1000)
    removed = list(
        await db.scalars(
            delete(RemoteLaunchRequest)
            .where(
                RemoteLaunchRequest.id.in_(ids),
            )
            .returning(RemoteLaunchRequest.id)
        )
    )
    audits = (
        select(DelegationAudit.id)
        .where(
            DelegationAudit.event.like("launch.%"),
            DelegationAudit.created_at < cutoff,
        )
        .limit(1000)
    )
    await db.execute(delete(DelegationAudit).where(DelegationAudit.id.in_(audits)))
    await db.commit()
    return len(removed)
