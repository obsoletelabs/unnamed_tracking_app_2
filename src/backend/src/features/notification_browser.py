"""Reviewed browser subscriptions and the existing authenticated PWA boundary."""

import base64
import json
import re
import time
from dataclasses import dataclass
from urllib.parse import urlsplit
from uuid import UUID

from cryptography.fernet import InvalidToken
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import String, cast, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.auth import hash_token, session_cookie_name
from src.core.crypto import decrypt_secret
from src.database.models.auth import UserSession
from src.database.models.notification_destination import NotificationDestination
from src.database.models.user import User
from src.features.notification_lifecycle import invalidate_endpoint
from src.features.notification_push_config import (
    PUSH_PROVIDER,
    PushConfiguration,
    push_configuration,
)
from src.plugin_api import pwa
from src.plugin_api.runtime_client import PluginRuntimeUnavailable


def _decode_key(value: str, length: int) -> bytes:
    if not re.fullmatch(r"[A-Za-z0-9_-]+={0,2}", value):
        raise ValueError("Invalid browser subscription key")
    raw = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
    if len(raw) != length:
        raise ValueError("Invalid browser subscription key length")
    return raw


class BrowserKeys(BaseModel):
    model_config = ConfigDict(extra="forbid")
    p256dh: str = Field(min_length=86, max_length=88, repr=False)
    auth: str = Field(min_length=22, max_length=24, repr=False)

    @field_validator("p256dh")
    @classmethod
    def public_key(cls, value: str) -> str:
        ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), _decode_key(value, 65))
        return value

    @field_validator("auth")
    @classmethod
    def authentication_secret(cls, value: str) -> str:
        _decode_key(value, 16)
        return value


class BrowserSubscription(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    endpoint: str = Field(min_length=1, max_length=2048, repr=False)
    keys: BrowserKeys
    expiration_time: int | None = Field(default=None, alias="expirationTime", ge=0)

    @field_validator("endpoint")
    @classmethod
    def reviewed_service(cls, value: str) -> str:
        if any(ord(character) <= 32 or ord(character) >= 127 for character in value):
            raise ValueError("Invalid browser push endpoint")
        parsed = urlsplit(value)
        host = parsed.hostname or ""
        supported = host in {
            "fcm.googleapis.com",
            "updates.push.services.mozilla.com",
            "web.push.apple.com",
        } or bool(re.fullmatch(r"[a-z0-9-]+\.notify\.windows\.com", host))
        safe_authority = (
            parsed.scheme == "https"
            and supported
            and not (parsed.username or parsed.password)
            and parsed.port in {None, 443}
        )
        if (
            not safe_authority
            or parsed.fragment
            or not parsed.path.startswith("/")
            or "\\" in value
        ):
            raise ValueError("Use a supported HTTPS browser push subscription")
        return value

    @property
    def expired(self) -> bool:
        return self.expiration_time is not None and self.expiration_time <= int(time.time() * 1000)


@dataclass(frozen=True)
class BrowserContext:
    installation_id: UUID
    generation: str
    configuration: PushConfiguration


async def browser_context(db: AsyncSession) -> BrowserContext | None:
    configuration = await push_configuration(db)
    if not configuration.configured:
        return None
    try:
        plugin = await pwa.provider(db)
    except HTTPException as exc:
        if exc.status_code == 503:
            raise PluginRuntimeUnavailable("PWA runtime unavailable") from exc
        return None
    return (
        BrowserContext(UUID(str(plugin["installation_id"])), pwa.generation(plugin), configuration)
        if plugin
        else None
    )


async def browser_session(db: AsyncSession, request: Request, user_id: UUID) -> UserSession | None:
    actor = getattr(request.state, "authenticated_actor", None)
    if not actor or actor.credential_kind != "session" or actor.user_id != user_id:
        return None
    token = request.cookies.get(session_cookie_name(request.headers.get("host", "")))
    return await db.scalar(
        select(UserSession)
        .where(
            UserSession.token_hash == hash_token(token or ""),
            UserSession.user_id == user_id,
            UserSession.expires_at > int(time.time()),
            UserSession.revoked_at.is_(None),
        )
        .execution_options(populate_existing=True)
    )


async def bound_subscription(
    db: AsyncSession,
    endpoint: NotificationDestination,
    context: BrowserContext,
    *,
    require_enabled: bool = True,
) -> BrowserSubscription | None:
    encrypted = endpoint.encrypted_configuration
    if not encrypted:
        return None
    if not all(
        (
            endpoint.provider_id == PUSH_PROVIDER,
            endpoint.kind == "browser_push",
            endpoint.active,
            endpoint.enabled or not require_enabled,
            endpoint.installation_id == context.installation_id,
        )
    ):
        return None
    try:
        session_id = UUID(endpoint.configuration_ref or "")
        config = json.loads(decrypt_secret(encrypted))
        subscription = BrowserSubscription.model_validate(config["subscription"])
    except (ValueError, KeyError, TypeError, InvalidToken):
        return None
    if config.get("public_key") != context.configuration.public_key or subscription.expired:
        return None
    session = await db.scalar(
        select(UserSession)
        .join(User, User.id == UserSession.user_id)
        .where(
            UserSession.id == session_id,
            UserSession.user_id == endpoint.user_id,
            UserSession.expires_at > int(time.time()),
            UserSession.revoked_at.is_(None),
            User.is_active.is_(True),
        )
        .execution_options(populate_existing=True)
    )
    if session is None:
        return None
    return subscription


async def retire_browser(db: AsyncSession, endpoint: NotificationDestination, reason: str) -> None:
    await invalidate_endpoint(db, endpoint, reason)
    endpoint.active = False
    endpoint.enabled = False
    endpoint.encrypted_configuration = None
    endpoint.endpoint_key = f"retired:{endpoint.id}"


async def expire_browser_sessions(db: AsyncSession, limit: int = 100) -> int:
    """Bound cleanup on the existing job tick; expired auth cannot remain a destination."""
    endpoints = list(
        await db.scalars(
            select(NotificationDestination)
            .outerjoin(
                UserSession,
                NotificationDestination.configuration_ref == cast(UserSession.id, String),
            )
            .where(
                NotificationDestination.provider_id == PUSH_PROVIDER,
                NotificationDestination.active.is_(True),
                or_(
                    UserSession.id.is_(None),
                    UserSession.revoked_at.is_not(None),
                    UserSession.expires_at <= int(time.time()),
                ),
            )
            .limit(limit)
            .with_for_update(skip_locked=True, of=NotificationDestination)
        )
    )
    for endpoint in endpoints:
        await retire_browser(db, endpoint, "browser_session_expired")
    return len(endpoints)


async def withdraw_previous_browser(db: AsyncSession, request: Request) -> None:
    """Replacing this browser's sign-in cookie withdraws its old push enrollment."""
    token = request.cookies.get(session_cookie_name(request.headers.get("host", "")))
    if not token:
        return
    previous_id = await db.scalar(
        select(UserSession.id).where(UserSession.token_hash == hash_token(token))
    )
    if previous_id is None:
        return
    endpoints = list(
        await db.scalars(
            select(NotificationDestination).where(
                NotificationDestination.provider_id == PUSH_PROVIDER,
                NotificationDestination.active.is_(True),
                NotificationDestination.configuration_ref == str(previous_id),
            )
        )
    )
    for endpoint in endpoints:
        await retire_browser(db, endpoint, "browser_signin_changed")
