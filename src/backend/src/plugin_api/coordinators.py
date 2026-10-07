"""Core-owned provider contracts exposed through Plugin API v1.

These protocols describe the extension seam only. The core application keeps
provider selection, user preferences, retries, persistence and orchestration.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from .contracts import API_VERSION
from .metadata_contracts import MetadataCandidate, MetadataProviderRequest


class CoordinatorModel(BaseModel):
    """Wire-safe coordinator DTO."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class NotificationRequest(CoordinatorModel):
    """Request for a provider to deliver a core-generated notification."""

    notification_id: UUID
    user_id: UUID
    title: str = Field(min_length=1, max_length=512)
    body: str = Field(min_length=1, max_length=10_000)
    created_at: datetime


class NotificationResult(CoordinatorModel):
    """Provider result returned to the core coordinator."""

    delivered: bool
    external_id: str | None = Field(default=None, max_length=512)
    detail: str | None = Field(default=None, max_length=1024)


class NotificationProvider(Protocol):
    """Plugin implementation of the core notification-provider seam."""

    # Each provider protocol deliberately exposes one extension operation.
    # pylint: disable=too-few-public-methods

    api_version: str

    async def send(self, request: NotificationRequest) -> NotificationResult:
        """Deliver a core-owned notification without owning orchestration."""


class MetadataProvider(Protocol):
    """Plugin implementation of the core metadata-provider seam."""

    # Each provider protocol deliberately exposes one extension operation.
    # pylint: disable=too-few-public-methods

    api_version: str

    async def search(self, request: MetadataProviderRequest) -> list[MetadataCandidate]:
        """Return normalized candidates; selection remains core-owned."""


__all__ = [
    "API_VERSION",
    "MetadataCandidate",
    "MetadataProvider",
    "MetadataProviderRequest",
    "NotificationProvider",
    "NotificationRequest",
    "NotificationResult",
]
