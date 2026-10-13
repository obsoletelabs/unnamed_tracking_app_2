"""Host-held, user-owned delegated connections to independently hosted instances."""

import asyncio
import hashlib
import secrets
import time
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.schemas.delegation import SCOPE_DESCRIPTIONS, integration_origin
from src.core.auth import hash_token
from src.core.config import settings
from src.core.crypto import decrypt_secret, encrypt_secret
from src.database.models.public_app import PublicAppActor, PublicAppSource
from src.features import legacy_integrations
from src.features.delegation import integration_policy
from src.plugin_api.outbound import public_instance_request


async def remote(source: PublicAppSource, path: str, **options) -> dict:
    try:
        return await asyncio.to_thread(public_instance_request, source.origin, path, **options)
    except (OSError, ValueError, TimeoutError) as exc:
        raise HTTPException(
            503, "The instance is unreachable or returned an invalid response."
        ) from exc


def source_summary(source: PublicAppSource) -> dict:
    return {
        "id": str(source.id),
        "origin": source.origin,
        "instance_id": source.instance_id,
        "state": source.state,
        "scopes": source.scopes,
        "expires_at": source.expires_at,
        "last_checked_at": source.last_checked_at,
        "game_count": len(source.library),
        "credential_kind": source.credential_kind,
    }


async def owned_source(db: AsyncSession, actor: PublicAppActor, source_id: UUID) -> PublicAppSource:
    row = await db.scalar(
        select(PublicAppSource)
        .where(
            PublicAppSource.actor_id == actor.id,
            PublicAppSource.id == source_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None:
        raise HTTPException(404, "Connection not found.")
    return row


async def connect_source(db: AsyncSession, actor: PublicAppActor, origin: str) -> dict:
    origin = integration_origin(origin)
    existing = list(
        await db.scalars(select(PublicAppSource).where(PublicAppSource.actor_id == actor.id))
    )
    if len(existing) >= 5 and not any(row.origin == origin for row in existing):
        raise HTTPException(422, "Disconnect an instance before adding a sixth connection.")
    # Source approval always names the actual operator origin, never Discord's proxy.
    operator_origin = integration_origin(settings.PUBLIC_APP_URL)
    try:
        result = await asyncio.to_thread(
            public_instance_request, origin, "/api/delegation/v1/instance"
        )
    except (OSError, ValueError, TimeoutError) as exc:
        raise HTTPException(503, "Cannot reach this instance's delegated integration API.") from exc
    info = result.get("data", {})
    if (
        result["status"] != 200
        or not isinstance(info, dict)
        or not info.get("enabled")
        or info.get("protocol_version") != "1.0"
    ):
        raise HTTPException(
            409, "Upgrade this instance and enable user-delegated integrations first."
        )
    client = next(
        (
            item
            for item in info.get("clients", [])
            if isinstance(item, dict) and item.get("origin") == operator_origin
        ),
        None,
    )
    if client is None:
        if info.get("allow_any_origin") is True:
            registration = await asyncio.to_thread(
                public_instance_request,
                origin,
                "/api/delegation/v1/clients/register",
                method="POST",
                body={"name": actor.plugin_id[:100], "origin": operator_origin},
            )
            if registration["status"] == 201:
                client = registration["data"]
    if client is None:
        raise HTTPException(
            403, f"The owner must approve {operator_origin} on this instance first."
        )
    verifier = secrets.token_urlsafe(48)
    source = next((row for row in existing if row.origin == origin), None)
    if source is None:
        source = PublicAppSource(actor_id=actor.id, origin=origin)
        db.add(source)
    source.instance_id = str(UUID(info["instance_id"]))
    request_result = await remote(
        source,
        "/api/delegation/v1/requests",
        method="POST",
        body={
            "client_id": client["id"],
            "scopes": sorted(SCOPE_DESCRIPTIONS),
            "code_challenge": hash_token(verifier),
        },
    )
    if request_result["status"] != 201:
        raise HTTPException(409, "The instance could not create an access request.")
    consent = request_result["data"]
    source.request_id = UUID(consent["request_id"])
    source.credential_kind = "delegated"
    source.remote_user_id = None
    source.verifier = encrypt_secret(verifier)
    source.credential = None
    source.library = []
    source.scopes = []
    source.state = "pending"
    source.expires_at = consent["expires_at"]
    source.last_checked_at = None
    await db.commit()
    return {
        **source_summary(source),
        "consent_url": origin + f"/integration-access/{source.request_id}",
        "poll_interval_seconds": 5,
    }


async def poll_source(db: AsyncSession, source: PublicAppSource) -> dict:
    now = int(time.time())
    if source.state != "pending" or source.verifier is None:
        return source_summary(source)
    if source.expires_at is not None and source.expires_at <= now:
        source.state, source.verifier = "expired", None
        await db.commit()
        return source_summary(source)
    if source.last_checked_at and source.last_checked_at + 5 > now:
        return source_summary(source)
    source.last_checked_at = now
    result = await remote(
        source,
        f"/api/delegation/v1/requests/{source.request_id}/exchange",
        method="POST",
        body={"verifier": decrypt_secret(source.verifier)},
    )
    if result["status"] != 200:
        source.state, source.verifier = "expired", None
    else:
        data = result["data"]
        if data.get("state") == "connected":
            source.credential = encrypt_secret(data["access_token"])
            source.scopes = data["scopes"]
            source.expires_at = data["expires_at"]
            source.state, source.verifier = "connected", None
        elif data.get("state") == "denied":
            source.state, source.verifier = "denied", None
    await db.commit()
    return source_summary(source)


async def connect_legacy_source(
    db: AsyncSession,
    actor: PublicAppActor,
    origin: str,
    api_key: str,
    acknowledge_unrestricted_key: bool,
) -> dict:
    policy = await integration_policy(db)
    if not policy.allow_legacy_api_keys:
        raise HTTPException(403, "The Activity operator has disabled legacy API-key connections.")
    if not acknowledge_unrestricted_key:
        raise HTTPException(422, "Acknowledge the unrestricted API key before connecting.")
    await db.scalar(
        select(PublicAppActor.id).where(PublicAppActor.id == actor.id).with_for_update()
    )
    existing = list(
        await db.scalars(select(PublicAppSource).where(PublicAppSource.actor_id == actor.id))
    )
    source = next((row for row in existing if row.origin == origin), None)
    if source is None:
        if len(existing) >= 5:
            raise HTTPException(422, "Disconnect an instance before adding a sixth connection.")
        source = PublicAppSource(actor_id=actor.id, origin=origin)
        db.add(source)
    source.credential_kind = "legacy_api_key"
    source.instance_id = "legacy:" + hashlib.sha256(origin.encode()).hexdigest()[:32]
    source.request_id, source.verifier, source.expires_at = None, None, None
    source.remote_user_id = None
    source.credential = encrypt_secret(api_key)
    source.scopes, source.library, source.state = [], [], "pending"
    await legacy_integrations.check(source, library=True)
    if source.state != "connected":
        raise HTTPException(401, "The legacy instance could not verify this API key.")
    await db.commit()
    return source_summary(source)


async def check_source(
    source: PublicAppSource,
    *,
    library: bool = False,
    allow_legacy: bool = False,
) -> None:
    """Live grant checks precede cached contributions and every remote mutation."""
    if not source.credential or (
        source.expires_at is not None and source.expires_at <= int(time.time())
    ):
        source.library, source.state, source.credential = [], "expired", None
        return
    token = decrypt_secret(source.credential)
    try:
        if source.credential_kind == "legacy_api_key":
            if not allow_legacy:
                source.library, source.state = [], "disabled"
                return
            await legacy_integrations.check(source, library=library)
            return
        result = await remote(source, "/api/delegation/v1/credential", credential=token)
        source.last_checked_at = int(time.time())
        if result["status"] in {401, 403}:
            source.library, source.state, source.credential = [], "revoked", None
            return
        if result["status"] != 200:
            source.state = "offline"
            return
        source.scopes = result["data"]["scopes"]
        source.expires_at = result["data"]["expires_at"]
        if "games.read" not in source.scopes:
            source.library = []
        elif library:
            games, offset = [], 0
            for _page in range(10):
                page = await remote(
                    source, f"/api/delegation/v1/games?offset={offset}&limit=200", credential=token
                )
                if page["status"] != 200:
                    source.state = "offline"
                    return
                games.extend(page["data"]["games"])
                if page["data"]["complete"]:
                    break
                offset = page["data"]["next_offset"]
            else:
                raise HTTPException(422, "This library exceeds the Activity's 2,000-game limit.")
            source.library = games
        source.state = "connected"
    except HTTPException:
        source.state = "offline"


async def source_request(source: PublicAppSource, path: str, **options) -> dict:
    if source.credential_kind == "legacy_api_key":
        raise HTTPException(
            409,
            "Legacy connections support library reads only. Use scoped consent for this operation.",
        )
    if not source.credential:
        raise HTTPException(401, "Reconnect this instance first.")
    result = await remote(source, path, credential=decrypt_secret(source.credential), **options)
    if result["status"] not in {200, 201, 204}:
        raise HTTPException(
            result["status"] if result["status"] in {401, 403, 404, 422} else 503,
            "The instance rejected this operation; check access and reconnect if needed.",
        )
    return result
