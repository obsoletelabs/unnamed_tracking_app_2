"""Resolve active notification providers from durable plugin registrations."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models.plugin_notification_provider import (
    PluginNotificationProviderRegistration,
)
from src.plugin_api.runtime_client import PluginRuntimeClient

from .base import NotificationProvider
from .plugin import PluginNotificationProvider
from .smtp import SmtpNotificationProvider


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
    providers: dict[str, NotificationProvider] = {
        SmtpNotificationProvider.id: SmtpNotificationProvider()
    }
    if not registrations:
        return providers
    runtime = PluginRuntimeClient()
    providers.update(
        {
            registration.provider_id: PluginNotificationProvider(registration, runtime=runtime)
            for registration in registrations
        }
    )
    return providers
