"""Explicit owner enrollment for host-protected plugin webhook destinations."""

import hashlib
import hmac
import json
import time
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import settings
from src.core.crypto import encrypt_secret
from src.database.models.notification_destination import NotificationDestination
from src.database.models.notification_provider_setting import NotificationProviderSetting
from src.database.models.plugin_notification_provider import PluginNotificationProviderRegistration
from src.database.models.user import User
from src.features.notification_enrollment import (
    EnrollmentError,
    enforce_destination_limit,
    invalidate_endpoint,
)
from src.features.notification_providers.plugin import PluginNotificationProvider
from src.features.notification_providers.webhook import normalize_discord_webhook


async def _owned(db: AsyncSession, user_id: UUID, destination_id: UUID) -> NotificationDestination:
    await db.scalar(select(User.id).where(User.id == user_id).with_for_update())
    endpoint = await db.scalar(
        select(NotificationDestination)
        .where(
            NotificationDestination.id == destination_id,
            NotificationDestination.user_id == user_id,
            NotificationDestination.kind == "discord_webhook",
        )
        .with_for_update()
    )
    if endpoint is None:
        raise EnrollmentError("Webhook destination not found", 404)
    return endpoint


async def _registration(db: AsyncSession, user_id: UUID, provider_id: str):
    row = await db.scalar(
        select(PluginNotificationProviderRegistration).where(
            PluginNotificationProviderRegistration.provider_id == provider_id,
            PluginNotificationProviderRegistration.transport == "discord_webhook",
            PluginNotificationProviderRegistration.revoked_at.is_(None),
        )
    )
    if row is None or not await PluginNotificationProvider(row).is_authorized(db, user_id):
        raise EnrollmentError("This webhook provider is unavailable or not permitted", 403)
    return row


async def create_webhook(
    db: AsyncSession, user_id: UUID, provider_id: str, url: str, label: str
) -> UUID:
    url = normalize_discord_webhook(url)
    await db.scalar(select(User.id).where(User.id == user_id).with_for_update())
    registration = await _registration(db, user_id, provider_id)
    await enforce_destination_limit(db, user_id, provider_id)
    key = hmac.new(settings.SECRET_KEY.encode(), url.encode(), hashlib.sha256).hexdigest()
    duplicate = await db.scalar(
        select(NotificationDestination.id).where(
            NotificationDestination.user_id == user_id,
            NotificationDestination.provider_id == provider_id,
            NotificationDestination.endpoint_key == key,
            NotificationDestination.active.is_(True),
        )
    )
    if duplicate:
        raise EnrollmentError("This webhook already has an active destination", 409)
    # A retired identity is never reclaimed with its old credentials or consent.
    # Fresh enrollment receives a new ID even when the owner supplies the same URL.
    retired = await db.scalar(
        select(NotificationDestination).where(
            NotificationDestination.user_id == user_id,
            NotificationDestination.provider_id == provider_id,
            NotificationDestination.endpoint_key == key,
        )
    )
    if retired:
        retired.endpoint_key = f"retired:{retired.id}"
    endpoint = NotificationDestination(
        user_id=user_id,
        provider_id=provider_id,
        endpoint_key=key,
        kind="discord_webhook",
        channel_context="external",
        privacy=0,
        installation_id=registration.installation_id,
        display_name=label.strip() or "Discord webhook",
        encrypted_configuration=encrypt_secret(json.dumps({"webhook": url})),
    )
    db.add(endpoint)
    setting = await db.scalar(
        select(NotificationProviderSetting).where(
            NotificationProviderSetting.user_id == user_id,
            NotificationProviderSetting.provider_id == provider_id,
        )
    )
    if setting is None:
        db.add(NotificationProviderSetting(user_id=user_id, provider_id=provider_id, enabled=True))
    await db.commit()
    return endpoint.id


async def update_webhook(
    db: AsyncSession, user_id: UUID, destination_id: UUID, changes: dict
) -> None:
    endpoint = await _owned(db, user_id, destination_id)
    registration = await _registration(db, user_id, endpoint.provider_id)
    if (
        endpoint.installation_id != registration.installation_id
        or not endpoint.encrypted_configuration
    ):
        raise EnrollmentError(
            "Enroll a new webhook after reinstalling or re-registering this provider"
        )
    if not endpoint.active:
        if changes.get("enabled") is not True:
            raise EnrollmentError(
                "Explicitly activate this destination before changing its routing"
            )
        await invalidate_endpoint(db, endpoint)
        endpoint.active = True
    if "label" in changes:
        endpoint.display_name = changes["label"].strip() or "Discord webhook"
    if "enabled" in changes:
        endpoint.enabled = changes["enabled"]
        if not endpoint.enabled:
            await invalidate_endpoint(db, endpoint)
    if "share_followed_media" in changes:
        # Consent affects this media projection, never trust or security routing.
        await invalidate_endpoint(db, endpoint)
        if changes["share_followed_media"]:
            endpoint.media_consent_revision = endpoint.revision
            endpoint.media_consent_at = int(time.time())
    await db.commit()


async def remove_webhook(db: AsyncSession, user_id: UUID, destination_id: UUID) -> None:
    endpoint = await _owned(db, user_id, destination_id)
    await invalidate_endpoint(db, endpoint)
    endpoint.active = False
    endpoint.enabled = False
    endpoint.encrypted_configuration = None
    endpoint.endpoint_key = f"retired:{endpoint.id}"
    await db.commit()
