"""Host-owned endpoint resolution without network calls or credential disclosure."""

from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from src.database.models.notification_delivery import NotificationDelivery
from src.database.models.notification_destination import NotificationDestination
from src.database.models.notification_provider_setting import NotificationProviderSetting
from src.database.models.plugin_notification_provider import PluginNotificationProviderRegistration
from src.features.notification_policy import INBOX_PROVIDER, Trust


async def invalidate_legacy_configuration(db: AsyncSession, installation_id: UUID) -> None:
    """A shared plugin secret change invalidates every endpoint bound to that installation.

    Disable before the runtime write, even if that write later fails. No old
    consent, proof or queued work can silently move to the replacement endpoint.
    The caller commits this boundary before contacting the runtime.
    """
    provider_ids = select(PluginNotificationProviderRegistration.provider_id).where(
        PluginNotificationProviderRegistration.installation_id == installation_id
    )
    destination_ids = list(
        await db.scalars(
            update(NotificationDestination)
            .where(
                NotificationDestination.installation_id == installation_id,
                NotificationDestination.provider_id.in_(provider_ids),
                NotificationDestination.kind == "legacy_webhook",
            )
            .values(
                revision=NotificationDestination.revision + 1,
                enabled=False,
                verified_revision=None,
                verification_method=None,
                media_consent_revision=None,
                media_consent_at=None,
            )
            .returning(NotificationDestination.id)
        )
    )
    if destination_ids:
        await db.execute(
            update(NotificationDelivery)
            .where(
                NotificationDelivery.destination_id.in_(destination_ids),
                NotificationDelivery.status.in_(("pending", "processing", "retry_wait")),
            )
            .values(
                status="suppressed",
                last_error="endpoint_changed",
                claim_token=None,
                lease_until=None,
            )
        )


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
    allowed: ColumnElement[bool] = NotificationDestination.installation_id.is_(None)
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
