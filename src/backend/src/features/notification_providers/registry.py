"""Resolve active notification providers from durable plugin registrations."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models.plugin_notification_provider import (
    PluginNotificationProviderRegistration,
)
from src.plugin_api.runtime_client import PluginRuntimeClient

from .base import NotificationProvider
from .plugin import PluginNotificationProvider


async def get_notification_providers(
    db: AsyncSession,
) -> dict[str, NotificationProvider]:
    registrations = (
        await db.scalars(
            select(PluginNotificationProviderRegistration).where(
                PluginNotificationProviderRegistration.revoked_at.is_(None)
            )
        )
    ).all()
    if not registrations:
        return {}
    runtime = PluginRuntimeClient()
    return {
        registration.provider_id: PluginNotificationProvider(registration, runtime=runtime)
        for registration in registrations
    }
