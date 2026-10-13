"""Host infrastructure for permissioned public apps and isolated external identities."""

from __future__ import annotations

import secrets
import time
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import HTTPException, Request
from sqlalchemy import String, cast, delete, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.auth import hash_token
from src.database.models.plugin_permissions import PluginPermissionGrant
from src.database.models.public_app import PublicAppActor, PublicAppSession, PublicAppSource
from src.features.delegation import integration_policy
from src.plugin_api.contracts import PluginIdentityProviderDeclaration, PluginPublicAppDeclaration
from src.plugin_api.grants import installation_is_executable
from src.plugin_api.runtime_client import (
    PluginRuntimeClient,
    PluginRuntimeRequestError,
    PluginRuntimeUnavailable,
)

client = PluginRuntimeClient()
SESSION_TTL = 1800


async def purge_expired_data(db: AsyncSession) -> None:
    """Forget external identity and credentials; keep only inert principal IDs."""
    now = int(time.time())
    # Approved account connections survive guest retention so a verified Discord
    # identity can reconnect. Room sessions still expire independently.
    linked = select(PublicAppSource.actor_id).where(
        PublicAppSource.credential.is_not(None),
        PublicAppSource.state.in_(["connected", "offline"]),
        or_(PublicAppSource.expires_at.is_(None), PublicAppSource.expires_at > now),
    )
    expired = select(PublicAppActor.id).where(
        PublicAppActor.expires_at <= now, PublicAppActor.id.not_in(linked)
    )
    await db.execute(delete(PublicAppSource).where(PublicAppSource.actor_id.in_(expired)))
    await db.execute(
        delete(PublicAppSession).where(
            (PublicAppSession.expires_at <= now) | PublicAppSession.actor_id.in_(expired)
        )
    )
    await db.execute(
        update(PublicAppActor)
        .where(PublicAppActor.id.in_(expired))
        .values(
            subject="expired:" + cast(PublicAppActor.id, String),
            display_name="",
            avatar_url=None,
        )
    )
    await db.commit()


async def installation(
    db: AsyncSession,
    plugin_id: str,
    capability: str,
    declaration_key: str,
) -> dict[str, Any]:
    """Require a current healthy package and exact installation-wide critical grant."""
    try:
        plugins = await client.plugins()
    except (PluginRuntimeRequestError, PluginRuntimeUnavailable) as exc:
        raise HTTPException(503, "The public-app runtime is unavailable.") from exc
    plugin = next((item for item in plugins if item.get("plugin_id") == plugin_id), None)
    if plugin is None or not installation_is_executable(plugin) or not plugin.get(declaration_key):
        raise HTTPException(404, "This public app or identity provider is unavailable.")
    try:
        installation_id = UUID(str(plugin["installation_id"]))
        declaration = (
            PluginPublicAppDeclaration.model_validate(plugin[declaration_key])
            if declaration_key == "public_app"
            else PluginIdentityProviderDeclaration.model_validate(plugin[declaration_key])
        )
    except (ValueError, KeyError) as exc:
        raise HTTPException(503, "The public-app declaration is invalid.") from exc
    grant = await db.scalar(
        select(PluginPermissionGrant.id).where(
            PluginPermissionGrant.plugin_id == plugin_id,
            PluginPermissionGrant.installation_id == installation_id,
            PluginPermissionGrant.capability == capability,
            PluginPermissionGrant.capability_version == 1,
            PluginPermissionGrant.user_id.is_(None),
            PluginPermissionGrant.device_id.is_(None),
            PluginPermissionGrant.revoked_at.is_(None),
        )
    )
    if grant is None:
        raise HTTPException(403, "The owner has not approved this public-app capability.")
    return {**plugin, "declaration": declaration}


async def app_installation(db: AsyncSession, plugin_id: str) -> dict[str, Any]:
    policy = await integration_policy(db)
    if not policy.enabled:
        raise HTTPException(403, "The owner has disabled integrations.")
    return await installation(db, plugin_id, "integrations.public_app", "public_app")


async def identity_provider(db: AsyncSession, app: dict[str, Any]) -> dict[str, Any]:
    return await installation(
        db,
        app["declaration"].identity_provider_plugin_id,
        "integrations.identity_provider",
        "identity_provider",
    )


async def provider_action(provider: dict[str, Any], action_id: str, values: dict) -> dict:
    """Only the explicit trusted adapter uses its configured server credentials."""
    user_id = provider.get("background_user_id")
    if not user_id:
        raise HTTPException(503, "Enable the identity provider with a server identity first.")
    try:
        return await client.action(provider["plugin_id"], action_id, values, user_id=str(user_id))
    except (PluginRuntimeRequestError, PluginRuntimeUnavailable) as exc:
        raise HTTPException(
            401, "External identity or app-room membership could not be verified."
        ) from exc


async def bootstrap(db: AsyncSession, plugin_id: str, proof: dict) -> dict:
    app = await app_installation(db, plugin_id)
    provider = await identity_provider(db, app)
    verified = await provider_action(
        provider, provider["declaration"].authenticate_action_id, {"proof": proof}
    )
    subject, room, display_name = (
        verified.get("subject"),
        verified.get("room"),
        verified.get("display_name"),
    )
    if (
        any(
            not isinstance(value, str) or not value or len(value) > maximum
            for value, maximum in (
                (subject, 255),
                (room, 255),
                (display_name, 100),
            )
        )
        or verified.get("verified") is not True
    ):
        raise HTTPException(401, "The identity provider returned no verified participant.")
    now = int(time.time())
    policy = await integration_policy(db)
    avatar_url = verified.get("avatar_url")
    if avatar_url is not None and (
        not isinstance(avatar_url, str)
        or len(avatar_url) > 2048
        or urlsplit(avatar_url).scheme != "https"
        or urlsplit(avatar_url).username
        or urlsplit(avatar_url).password
    ):
        avatar_url = None
    await purge_expired_data(db)
    await db.execute(
        insert(PublicAppActor)
        .values(
            plugin_id=plugin_id,
            installation_id=UUID(app["installation_id"]),
            provider_installation_id=UUID(provider["installation_id"]),
            subject=subject,
            display_name=display_name,
            avatar_url=avatar_url,
            expires_at=now + policy.guest_retention_hours * 3600,
        )
        .on_conflict_do_update(
            index_elements=["installation_id", "subject"],
            set_={
                "display_name": display_name,
                "avatar_url": avatar_url,
                "provider_installation_id": UUID(provider["installation_id"]),
                "expires_at": now + policy.guest_retention_hours * 3600,
            },
        )
    )
    actor = await db.scalar(
        select(PublicAppActor).where(
            PublicAppActor.installation_id == UUID(app["installation_id"]),
            PublicAppActor.subject == subject,
        )
    )
    assert actor is not None
    token = "utp_" + secrets.token_urlsafe(48)
    db.add(
        PublicAppSession(
            actor_id=actor.id,
            token_hash=hash_token(token),
            room=room,
            verified_at=now,
            expires_at=now + SESSION_TTL,
        )
    )
    await db.commit()
    return {
        "session_token": token,
        "expires_at": now + SESSION_TTL,
        "participant": {"id": str(actor.id), "display_name": actor.display_name},
        "room": room,
        "allow_guests": policy.allow_guests,
        "sdk_authentication": verified.get("sdk_authentication"),
    }


async def authenticated(
    db: AsyncSession,
    plugin_id: str,
    request: Request,
) -> tuple[dict[str, Any], PublicAppActor, PublicAppSession]:
    authorization = request.headers.get("authorization", "")
    if not authorization.startswith("Bearer utp_") or len(authorization) > 300:
        raise HTTPException(401, "A verified public-app session is required.")
    result = (
        await db.execute(
            select(PublicAppActor, PublicAppSession)
            .join(
                PublicAppSession,
                PublicAppSession.actor_id == PublicAppActor.id,
            )
            .where(
                PublicAppSession.token_hash == hash_token(authorization[7:]),
                PublicAppSession.expires_at > int(time.time()),
                PublicAppActor.expires_at > int(time.time()),
                PublicAppActor.plugin_id == plugin_id,
            )
        )
    ).first()
    if result is None:
        raise HTTPException(401, "This public-app session is expired or disconnected.")
    actor, session = result
    app = await app_installation(db, plugin_id)
    provider = await identity_provider(db, app)
    if (
        str(actor.installation_id) != app["installation_id"]
        or str(actor.provider_installation_id) != provider["installation_id"]
    ):
        raise HTTPException(401, "The app or identity-provider installation changed; reconnect.")
    now = int(time.time())
    if session.verified_at + 60 <= now:
        verified = await provider_action(
            provider,
            provider["declaration"].revalidate_action_id,
            {"subject": actor.subject, "room": session.room},
        )
        if verified.get("verified") is not True:
            raise HTTPException(401, "You are no longer in this external app room.")
        session.verified_at = now
    session.expires_at = now + SESSION_TTL
    policy = await integration_policy(db)
    actor.expires_at = now + policy.guest_retention_hours * 3600
    await db.commit()
    return app, actor, session


async def actor_context(db: AsyncSession, actor: PublicAppActor, session: PublicAppSession) -> dict:
    policy = await integration_policy(db)
    sources = list(
        await db.scalars(select(PublicAppSource).where(PublicAppSource.actor_id == actor.id))
    )
    return {
        "actor_id": str(actor.id),
        "room": session.room,
        "display_name": actor.display_name,
        "avatar_path": f"/api/public-apps/{actor.plugin_id}/participants/{actor.id}/avatar"
        if actor.avatar_url
        else None,
        "allow_guests": policy.allow_guests,
        "retention_hours": policy.guest_retention_hours,
        "library": [
            {
                **item,
                "source_id": str(source.id),
                "instance_id": source.instance_id,
                "source_state": source.state,
                "source_checked_at": source.last_checked_at,
            }
            for source in sources
            if source.credential
            and (source.expires_at is None or source.expires_at > int(time.time()))
            for item in source.library
        ],
        "connected": any(source.state == "connected" for source in sources),
    }
