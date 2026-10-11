"""Host-issued possession proof for one configured plugin endpoint, never grouped accounts."""

import time
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models.notification import Notification
from src.database.models.user import User
from src.features.notification_enrollment import (
    EnrollmentError,
    confirm_destination_challenge,
    create_verification_challenge,
    owned_destination,
)
from src.features.notification_lifecycle import retire_destination_work
from src.features.notification_plugin_policy import plugin_sensitive_allowed
from src.features.notification_providers.plugin import PluginNotificationProvider
from src.features.notification_providers.registry import get_notification_providers
from src.plugin_api.runtime_client import PluginRuntimeUnavailable


async def _owned(db, user_id, destination_id):
    await db.scalar(select(User.id).where(User.id == user_id).with_for_update())
    endpoint = await owned_destination(
        db, user_id, destination_id, provider_id=None, active_only=False
    )
    if endpoint.installation_id is None:
        raise EnrollmentError("Notification destination not found", 404)
    return endpoint


async def _authorize(db, endpoint, *, purpose="verification"):
    notice = Notification(required_trust=1 if purpose == "verification" else 2, purpose=purpose)
    if (
        not endpoint.active
        or not endpoint.enabled
        or not await plugin_sensitive_allowed(db, notice, endpoint)
    ):
        raise EnrollmentError(
            "This destination cannot receive the requested sensitive content", 403
        )
    try:
        provider = (await get_notification_providers(db)).get(endpoint.provider_id)
        authorized = isinstance(
            provider, PluginNotificationProvider
        ) and await provider.is_authorized(db, endpoint.user_id)
    except PluginRuntimeUnavailable as exc:
        raise EnrollmentError("Plugin provider is unavailable", 503) from exc
    if not authorized:
        raise EnrollmentError("Plugin provider is unavailable", 403)


async def request_plugin_verification(
    db: AsyncSession, user_id: UUID, destination_id: UUID
) -> UUID:
    endpoint = await _owned(db, user_id, destination_id)
    await _authorize(db, endpoint)
    return await create_verification_challenge(db, endpoint)


async def confirm_plugin_verification(
    db: AsyncSession, user_id: UUID, destination_id: UUID, challenge_id: UUID, code: str
) -> bool:
    endpoint = await _owned(db, user_id, destination_id)
    await _authorize(db, endpoint)
    return await confirm_destination_challenge(
        db, endpoint, challenge_id, code, method="plugin.code.v1"
    )


async def revoke_plugin_verification(db: AsyncSession, user_id: UUID, destination_id: UUID) -> None:
    """Owner revocation works after uninstall and leaves ordinary PRIVATE configuration intact."""
    endpoint = await _owned(db, user_id, destination_id)
    endpoint.verified_revision = None
    endpoint.verification_method = None
    endpoint.verification_revoked_at = int(time.time())
    endpoint.recovery_allowed = False
    await retire_destination_work(db, endpoint.id, "verification_revoked")
    await db.commit()


async def set_plugin_recovery(
    db: AsyncSession, user_id: UUID, destination_id: UUID, allowed: bool
) -> None:
    endpoint = await _owned(db, user_id, destination_id)
    if allowed:
        await _authorize(db, endpoint, purpose="recovery")
        if (
            endpoint.verified_revision != endpoint.revision
            or endpoint.verification_revoked_at is not None
        ):
            raise EnrollmentError("Verify this destination before allowing recovery", 403)
    endpoint.recovery_allowed = allowed
    await db.commit()
