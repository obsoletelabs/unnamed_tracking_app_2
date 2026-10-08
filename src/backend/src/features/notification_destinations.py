"""Host-owned endpoint resolution without network calls or credential disclosure."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models.notification_destination import NotificationDestination
from src.database.models.notification_provider_setting import NotificationProviderSetting
from src.database.models.plugin_notification_provider import PluginNotificationProviderRegistration
from src.features.notification_policy import INBOX_PROVIDER, Trust


async def resolve_destinations(db: AsyncSession, user_id: UUID) -> list[NotificationDestination]:
    """Enroll the authenticated inbox and conservative opted-in legacy endpoints."""
    await db.execute(
        pg_insert(NotificationDestination)
        .values(
            user_id=user_id,
            provider_id=INBOX_PROVIDER,
            endpoint_key="inbox",
            kind="inbox",
            channel_context="internal",
            privacy=int(Trust.PRIVATE),
        )
        .on_conflict_do_nothing(index_elements=["user_id", "provider_id", "endpoint_key"])
    )
    registrations = (
        (
            await db.execute(
                select(PluginNotificationProviderRegistration)
                .join(
                    NotificationProviderSetting,
                    NotificationProviderSetting.provider_id
                    == PluginNotificationProviderRegistration.provider_id,
                )
                .where(
                    NotificationProviderSetting.user_id == user_id,
                    NotificationProviderSetting.enabled.is_(True),
                    PluginNotificationProviderRegistration.revoked_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    for registration in registrations:
        await db.execute(
            pg_insert(NotificationDestination)
            .values(
                user_id=user_id,
                provider_id=registration.provider_id,
                endpoint_key="legacy",
                kind="legacy_webhook",
                channel_context="external",
                privacy=int(Trust.PUBLIC),
                installation_id=registration.installation_id,
            )
            # An old installation owns its endpoint. Reinstall cannot silently
            # reactivate it or claim its credentials/preferences.
            .on_conflict_do_nothing(index_elements=["user_id", "provider_id", "endpoint_key"])
        )
    allowed = NotificationDestination.installation_id.is_(None)
    for registration in registrations:
        allowed = allowed | (
            (NotificationDestination.provider_id == registration.provider_id)
            & (NotificationDestination.installation_id == registration.installation_id)
        )
    return list(
        await db.scalars(
            select(NotificationDestination).where(
                NotificationDestination.user_id == user_id,
                allowed,
            )
        )
    )
