"""Retire notification trust that belonged to a replaced plugin signing identity.

A package replacement keeps the lifecycle instance (installation ID), so plugin
storage and the runtime identity continue. Host-held destination proof, consent
and queued sensitive work were granted to the previous publisher and must not
carry over to a differently signed package. Nothing here commits: the caller
owns the transaction so a failed replacement leaves the previous state intact.
"""

import time
from uuid import UUID

from sqlalchemy import String, cast, literal, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models.notification_destination import NotificationDestination
from src.database.models.plugin_notification_provider import PluginNotificationProviderRegistration
from src.features.notification_destinations import retire_plugin_destinations
from src.features.notification_source_policy import retire_sources
from src.features.plugin_auth_flows import retire_auth_flows


async def retire_replaced_identity(db: AsyncSession, plugin_id: str, installation_id: UUID) -> None:
    """Withdraw proof, consent, endpoint values and unsent work; require fresh activation."""
    provider_ids = list(
        await db.scalars(
            select(PluginNotificationProviderRegistration.provider_id).where(
                PluginNotificationProviderRegistration.plugin_id == plugin_id,
                PluginNotificationProviderRegistration.installation_id == installation_id,
            )
        )
    )
    await retire_auth_flows(db, plugin_id, installation_id)
    await retire_sources(db, plugin_id)
    # Unsent deliveries, pending verification links, SECURE proof, recovery and media consent.
    await retire_plugin_destinations(db, plugin_id, installation_id=installation_id)
    if provider_ids:
        # Mirror owner removal: a retired endpoint key keeps history but a fresh destination gets
        # a new ID, so configuration held under the old ID is never reachable by the replacement.
        await db.execute(
            update(NotificationDestination)
            .where(
                NotificationDestination.provider_id.in_(provider_ids),
                NotificationDestination.installation_id == installation_id,
                NotificationDestination.endpoint_key.not_like("retired:%"),
            )
            .values(
                revision=NotificationDestination.revision + 1,
                encrypted_configuration=None,
                endpoint_key=literal("retired:").concat(cast(NotificationDestination.id, String)),
                verification_revoked_at=int(time.time()),
            )
        )
        # The replacement registers its providers again; re-registration of a revoked row
        # retires any destination state once more and resets server configuration.
        await db.execute(
            update(PluginNotificationProviderRegistration)
            .where(
                PluginNotificationProviderRegistration.plugin_id == plugin_id,
                PluginNotificationProviderRegistration.installation_id == installation_id,
                PluginNotificationProviderRegistration.revoked_at.is_(None),
            )
            .values(
                revoked_at=int(time.time()),
                server_configured=False,
                server_revision=PluginNotificationProviderRegistration.server_revision + 1,
            )
        )
