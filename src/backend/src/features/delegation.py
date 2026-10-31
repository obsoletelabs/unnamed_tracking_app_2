"""Explicit user consent and one-use, verifier-bound integration credential exchange."""

import secrets
import time
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.functions import count

from src.api.schemas.delegation import (
    DESKTOP_ORIGINS,
    SCOPE_DESCRIPTIONS,
    AccessDecision,
    AccessRequest,
    ClientWrite,
    DesktopAccessRequest,
    ExternalAccount,
)
from src.core.auth import hash_token
from src.database.models.delegation import (
    DelegatedGrant,
    DelegationAudit,
    DelegationRequest,
    IntegrationClient,
    IntegrationPolicy,
)
from src.database.models.user import User

REQUEST_TTL_SECONDS = 600
TOKEN_PREFIX = "utd_"
WEB = "web"
DESKTOP = "desktop"
# First-party desktop apps the host knows by name. They have no web origin, so the
# operator's origin approval does not apply; `allow_desktop_apps` governs them and
# the user still approves every capability in their own browser.
DESKTOP_APPS = {"playnite": "Unnamed Tracking for Playnite"}
assert {f"{DESKTOP}:{app}" for app in DESKTOP_APPS} == DESKTOP_ORIGINS
# Capabilities that change data, shown as writes on the consent page.
WRITE_SCOPES = frozenset(
    {scope for scope in SCOPE_DESCRIPTIONS if scope.endswith(".write")}
    | {"library.sync", "games.launch"}
)


def client_allowed(policy: IntegrationPolicy, client: IntegrationClient) -> bool:
    if not client.enabled:
        return False
    if client.kind == DESKTOP:
        return policy.allow_desktop_apps
    return policy.enabled


async def integration_policy(db: AsyncSession) -> IntegrationPolicy:
    await db.execute(
        insert(IntegrationPolicy).values(id=1).on_conflict_do_nothing(index_elements=["id"])
    )
    row = await db.scalar(select(IntegrationPolicy).where(IntegrationPolicy.id == 1))
    assert row is not None
    return row


async def enabled_client(db: AsyncSession, client_id: UUID) -> IntegrationClient:
    policy = await integration_policy(db)
    client = await db.scalar(select(IntegrationClient).where(IntegrationClient.id == client_id))
    if client is None or not client_allowed(policy, client):
        raise HTTPException(403, "The instance owner has not enabled this integration.")
    return client


async def desktop_client(db: AsyncSession, app: str) -> IntegrationClient:
    """The client row of a known desktop app, created on first use.

    An operator who disabled the row (Integration access → clients) keeps it disabled."""
    if app not in DESKTOP_APPS:
        raise HTTPException(404, "Unknown desktop app.")
    policy = await integration_policy(db)
    if not policy.allow_desktop_apps:
        raise HTTPException(403, "The instance owner has turned off desktop app sign-in.")
    origin = f"{DESKTOP}:{app}"
    await db.execute(
        insert(IntegrationClient)
        .values(name=DESKTOP_APPS[app], origin=origin, enabled=True, kind=DESKTOP)
        .on_conflict_do_nothing(index_elements=["origin"])
    )
    client = await db.scalar(select(IntegrationClient).where(IntegrationClient.origin == origin))
    assert client is not None
    if client.kind != DESKTOP or not client.enabled:
        raise HTTPException(403, "The instance owner has disabled this desktop app.")
    return client


async def create_desktop_request(db: AsyncSession, app: str, payload: DesktopAccessRequest) -> dict:
    """Start consent for a desktop app; the device is shown to the user as display context."""
    client = await desktop_client(db, app)
    return await create_request(
        db,
        AccessRequest(
            client_id=client.id,
            scopes=payload.scopes,
            code_challenge=payload.code_challenge,
            external_account=ExternalAccount(
                provider=app, subject=payload.device_id, username=payload.device_name
            ),
        ),
    )


async def register_client(db: AsyncSession, payload: ClientWrite) -> dict:
    """Register a consent requester only under the owner's explicit open policy."""
    await integration_policy(db)
    # Serialize the registration bound and read the current policy after waiting.
    policy = await db.scalar(
        select(IntegrationPolicy)
        .where(IntegrationPolicy.id == 1)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    assert policy is not None
    if not policy.enabled or not policy.allow_any_origin:
        raise HTTPException(403, "The instance owner must approve this integration origin.")
    client = await db.scalar(
        select(IntegrationClient).where(IntegrationClient.origin == payload.origin)
    )
    if client is None:
        total = await db.scalar(select(count()).select_from(IntegrationClient))
        if total is not None and total >= 1000:
            raise HTTPException(429, "The instance has reached its registered integration limit.")
        client = IntegrationClient(origin=payload.origin, name=payload.name, enabled=True)
        db.add(client)
        await db.flush()
    if not client.enabled:
        raise HTTPException(403, "The owner has disabled this integration origin.")
    await db.commit()
    return {"id": str(client.id), "name": client.name, "origin": client.origin}


def record_audit(
    db: AsyncSession,
    event: str,
    *,
    client_id: UUID,
    user_id: UUID | None = None,
    grant_id: UUID | None = None,
) -> None:
    db.add(DelegationAudit(event=event, client_id=client_id, user_id=user_id, grant_id=grant_id))


async def create_request(db: AsyncSession, payload: AccessRequest) -> dict:
    client = await enabled_client(db, payload.client_id)
    # Serialize the pending limit, including concurrent first requests.
    await db.scalar(
        select(IntegrationClient.id).where(IntegrationClient.id == client.id).with_for_update()
    )
    now = int(time.time())
    pending = await db.scalar(
        select(count())
        .select_from(DelegationRequest)
        .where(
            DelegationRequest.client_id == client.id,
            DelegationRequest.expires_at > now,
            DelegationRequest.state == "pending",
        )
    )
    if pending and pending >= 100:
        raise HTTPException(429, "This integration has too many pending authorization requests.")
    row = DelegationRequest(
        client_id=client.id,
        requested_scopes=payload.scopes,
        code_challenge=payload.code_challenge,
        external_account=payload.external_account.model_dump()
        if payload.external_account
        else None,
        expires_at=now + REQUEST_TTL_SECONDS,
    )
    db.add(row)
    await db.flush()
    record_audit(db, "requested", client_id=client.id)
    await db.commit()
    return {
        "request_id": str(row.id),
        "expires_at": row.expires_at,
        "verification_path": f"/integration-access/{row.id}",
        "poll_interval_seconds": 5,
    }


async def pending_request(
    db: AsyncSession, request_id: UUID, *, lock: bool = False
) -> DelegationRequest:
    query = select(DelegationRequest).where(DelegationRequest.id == request_id)
    if lock:
        query = query.with_for_update()
    row = await db.scalar(query.execution_options(populate_existing=True))
    if row is None or row.expires_at <= int(time.time()):
        raise HTTPException(404, "This access request is unavailable or expired.")
    await enabled_client(db, row.client_id)
    return row


async def request_summary(db: AsyncSession, request_id: UUID) -> dict:
    row = await pending_request(db, request_id)
    client = await enabled_client(db, row.client_id)
    policy = await integration_policy(db)
    return {
        "id": str(row.id),
        "name": client.name,
        "origin": client.origin,
        "kind": client.kind,
        "instance_id": str(policy.instance_id),
        "state": row.state,
        "expires_at": row.expires_at,
        "maximum_grant_hours": policy.maximum_grant_hours,
        "external_account": row.external_account,
        "capabilities": [
            {
                "scope": scope,
                "description": SCOPE_DESCRIPTIONS[scope],
                "write": scope in WRITE_SCOPES,
            }
            for scope in row.requested_scopes
        ],
    }


async def decide_request(
    db: AsyncSession, request_id: UUID, user: User, payload: AccessDecision
) -> dict:
    row = await pending_request(db, request_id, lock=True)
    if row.state != "pending":
        raise HTTPException(409, "This access request has already been decided.")
    policy = await integration_policy(db)
    if not set(payload.scopes).issubset(row.requested_scopes):
        raise HTTPException(422, "Only the capabilities requested here can be approved.")
    if payload.approve:
        if not payload.scopes or (
            payload.expires_in_hours is not None
            and payload.expires_in_hours > policy.maximum_grant_hours
        ):
            raise HTTPException(
                422, "Choose capabilities and an expiry allowed by the instance owner."
            )
        grant = DelegatedGrant(
            user_id=user.id,
            client_id=row.client_id,
            request_id=row.id,
            scopes=payload.scopes,
            expires_at=(
                int(time.time()) + payload.expires_in_hours * 3600
                if payload.expires_in_hours is not None
                else None
            ),
        )
        db.add(grant)
        await db.flush()
        row.state = "approved"
        record_audit(db, "approved", client_id=row.client_id, user_id=user.id, grant_id=grant.id)
    else:
        row.state = "denied"
        record_audit(db, "denied", client_id=row.client_id, user_id=user.id)
    await db.commit()
    return {"state": row.state}


async def exchange_request(db: AsyncSession, request_id: UUID, verifier: str) -> dict:
    row = await pending_request(db, request_id, lock=True)
    if not secrets.compare_digest(row.code_challenge, hash_token(verifier)):
        raise HTTPException(401, "Invalid authorization exchange.")
    if row.state == "pending":
        return {"state": "pending"}
    if row.state == "denied":
        return {"state": "denied"}
    if row.state != "approved":
        raise HTTPException(409, "This access request has already been exchanged.")
    grant = await db.scalar(
        select(DelegatedGrant).where(DelegatedGrant.request_id == row.id).with_for_update()
    )
    if (
        grant is None
        or grant.revoked_at is not None
        or (grant.expires_at is not None and grant.expires_at <= int(time.time()))
    ):
        raise HTTPException(401, "This access grant is unavailable.")
    token = TOKEN_PREFIX + secrets.token_urlsafe(48)
    grant.token_hash = hash_token(token)
    row.state = "claimed"
    record_audit(db, "exchanged", client_id=row.client_id, user_id=grant.user_id, grant_id=grant.id)
    await db.commit()
    return {
        "state": "connected",
        "access_token": token,
        "grant_id": str(grant.id),
        "expires_at": grant.expires_at,
        "scopes": grant.scopes,
    }


async def authenticate_grant(db: AsyncSession, token: str) -> tuple[DelegatedGrant, User]:
    if not token.startswith(TOKEN_PREFIX) or len(token) > 256:
        raise HTTPException(401, "Invalid integration credentials.")
    policy = await integration_policy(db)
    result = (
        await db.execute(
            select(DelegatedGrant, User, IntegrationClient)
            .join(User, User.id == DelegatedGrant.user_id)
            .join(IntegrationClient, IntegrationClient.id == DelegatedGrant.client_id)
            .where(
                DelegatedGrant.token_hash == hash_token(token),
                DelegatedGrant.revoked_at.is_(None),
                or_(
                    DelegatedGrant.expires_at.is_(None),
                    DelegatedGrant.expires_at > int(time.time()),
                ),
                User.is_active.is_(True),
                IntegrationClient.enabled.is_(True),
            )
            .execution_options(populate_existing=True)
        )
    ).first()
    if result is None or not client_allowed(policy, result[2]):
        raise HTTPException(401, "The integration grant is expired, revoked or disabled.")
    return result[0], result[1]


async def owned_grant(db: AsyncSession, user_id: UUID, grant_id: UUID) -> DelegatedGrant:
    row = await db.scalar(
        select(DelegatedGrant)
        .where(
            DelegatedGrant.id == grant_id,
            DelegatedGrant.user_id == user_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None:
        raise HTTPException(404, "Integration grant not found.")
    return row


async def change_grant(
    db: AsyncSession, user_id: UUID, grant_id: UUID, scopes: list[str] | None
) -> dict:
    row = await owned_grant(db, user_id, grant_id)
    if scopes is None:
        row.revoked_at = int(time.time())
        row.token_hash = None
        event = "revoked"
    else:
        if row.revoked_at is not None or not set(scopes).issubset(row.scopes):
            raise HTTPException(
                422, "Access can only be reduced here; new access requires fresh consent."
            )
        row.scopes = scopes
        event = "reduced"
    record_audit(db, event, client_id=row.client_id, user_id=user_id, grant_id=row.id)
    await db.commit()
    return {"id": str(row.id), "scopes": row.scopes, "revoked_at": row.revoked_at}
