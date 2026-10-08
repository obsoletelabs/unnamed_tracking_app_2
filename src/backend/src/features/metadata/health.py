"""Nonblocking provider validation shared across identical credential contexts."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, replace
from uuid import uuid4

from sqlalchemy import select

from src.core.config import settings
from src.database.models.user import User
from src.database.session import SessionLocal
from src.plugin_api.metadata_contracts import MetadataProviderRequest, ProviderHealth

from .core import CoreMetadataProvider
from .deadlines import bounded
from .providers import PluginMetadataProvider, discover_providers

ProviderAdapter = PluginMetadataProvider | CoreMetadataProvider

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class HealthStatus:
    """Credential-context-specific diagnostics without provider exception messages."""

    state: ProviderHealth
    checked_at: float
    failure: str | None = None


class ProviderHealthMonitor:
    """Queue validation after configuration and at the configured periodic interval."""

    def __init__(self, interval: float = 1800) -> None:
        self.interval = max(60, interval)
        self.statuses: dict[tuple, HealthStatus] = {}
        self.tasks: dict[tuple, asyncio.Task] = {}
        self.validation_slots = asyncio.Semaphore(2)

    @staticmethod
    def key(provider: ProviderAdapter) -> tuple:
        return (provider.id, provider.installation_id, provider.revision, provider.user_id)

    def schedule(self, provider: ProviderAdapter, *, force: bool = False) -> None:
        if provider.state in {
            ProviderHealth.DISABLED,
            ProviderHealth.NOT_CONFIGURED,
            ProviderHealth.PLUGIN_UNAVAILABLE,
        }:
            return
        key = self.key(provider)
        previous = self.statuses.get(key)
        if key in self.tasks or (
            not force and previous and time.time() - previous.checked_at < self.interval
        ):
            return
        self.tasks[key] = asyncio.create_task(self._validate(provider))

    def status(self, provider: ProviderAdapter) -> HealthStatus:
        if provider.state in {
            ProviderHealth.DISABLED,
            ProviderHealth.NOT_CONFIGURED,
            ProviderHealth.PLUGIN_UNAVAILABLE,
        }:
            return HealthStatus(provider.state, 0)
        return self.statuses.get(self.key(provider), HealthStatus(provider.state, 0))

    def available(self, provider: ProviderAdapter) -> ProviderAdapter:
        self.schedule(provider)
        return replace(provider, state=self.status(provider).state)

    async def _validate(self, provider: ProviderAdapter) -> None:
        # Background validation cannot spawn a worker for every provider at once.
        # Queue outside the operation deadline; searches never wait for this queue.
        async with self.validation_slots:
            await self._validate_now(provider)

    async def _validate_now(self, provider: ProviderAdapter) -> None:
        failure = None
        try:
            response = await bounded(
                provider.invoke(
                    "health",
                    MetadataProviderRequest(
                        request_id=uuid4(),
                        user_id=provider.user_id,
                        query="health",
                        media_type=provider.declaration.media_types[0],
                        policy="background",
                    ),
                ),
                8,
            )
            state = response.health or ProviderHealth.HEALTHY
            if response.failure:
                failure = response.failure.code
                state = {
                    "not_configured": ProviderHealth.NOT_CONFIGURED,
                    "invalid_configuration": ProviderHealth.INVALID_CONFIGURATION,
                    "rate_limited": ProviderHealth.RATE_LIMITED,
                    "plugin_unavailable": ProviderHealth.PLUGIN_UNAVAILABLE,
                }.get(failure, ProviderHealth.UNAVAILABLE)
        except Exception:  # pylint: disable=broad-exception-caught
            state = ProviderHealth.UNAVAILABLE
            failure = "unavailable"
        finally:
            self.tasks.pop(self.key(provider), None)
        self.statuses[self.key(provider)] = HealthStatus(state, time.time(), failure)
        for key, status in list(self.statuses.items()):
            if time.time() - status.checked_at > max(self.interval * 2, 3600):
                self.statuses.pop(key, None)
        # Only bounded classifications enter logs, never provider response text.
        logger.debug("metadata_health provider=%s state=%s", provider.id, state)

    async def close(self) -> None:
        """Cancel validation during application shutdown without waiting on provider code."""
        for task in self.tasks.values():
            task.cancel()
        self.tasks.clear()

    async def run(self) -> None:
        """Discovery catches new installations without blocking startup or searches."""
        while True:
            try:
                async with SessionLocal() as db:
                    users = list(await db.scalars(select(User.id).where(User.is_active.is_(True))))
                    for user_id in users:
                        for provider in await discover_providers(db, user_id):
                            self.schedule(provider)
            except Exception:  # pylint: disable=broad-exception-caught
                logger.warning("Provider health discovery is temporarily unavailable")
            await asyncio.sleep(30)


monitor = ProviderHealthMonitor(settings.METADATA_HEALTH_INTERVAL_SECONDS)
