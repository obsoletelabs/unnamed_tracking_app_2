"""Lookup of a plugin installation's active canonical notification type declaration."""

from __future__ import annotations

from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models.plugin_notification_type import PluginNotificationTypeRegistration
from src.plugin_api.notification_plugin_contracts import CanonicalNotificationTypeRegistration


def canonical_declaration(definition: object) -> CanonicalNotificationTypeRegistration | None:
    """The stored declaration, or None for legacy template registrations and damaged rows."""
    if not isinstance(definition, dict) or "contract_version" not in definition:
        return None
    try:
        return CanonicalNotificationTypeRegistration.model_validate(definition)
    except ValidationError:
        return None


async def active_canonical_registration(
    db: AsyncSession, plugin_id: str, installation_id: UUID, event_type: str, *, lock: bool = False
) -> CanonicalNotificationTypeRegistration | None:
    statement = select(PluginNotificationTypeRegistration.definition).where(
        PluginNotificationTypeRegistration.event_type == event_type,
        PluginNotificationTypeRegistration.plugin_id == plugin_id,
        PluginNotificationTypeRegistration.installation_id == installation_id,
        PluginNotificationTypeRegistration.revoked_at.is_(None),
    )
    return canonical_declaration(
        await db.scalar(statement.with_for_update() if lock else statement)
    )
