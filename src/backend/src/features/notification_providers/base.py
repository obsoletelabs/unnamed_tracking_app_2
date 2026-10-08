"""Notification-provider contracts owned by the core application."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models.notification import Notification
from src.database.models.notification_provider_setting import NotificationProviderSetting
from src.database.models.user import User


@dataclass(frozen=True)
class NotificationMessage:
    id: UUID
    kind: str
    title: str
    body: str
    media_type: str
    media_id: UUID
    event_at: int
    attempt_id: UUID | None = None


@dataclass(frozen=True)
class ProviderDestination:
    user_id: UUID
    display: str


@dataclass(frozen=True)
class DeliveryResult:
    success: bool
    retryable: bool = False
    error: str | None = None


class NotificationProvider(Protocol):
    id: str
    name: str

    async def lookup_destination(
        self,
        db: AsyncSession,
        user: User,
        setting: NotificationProviderSetting | None,
    ) -> ProviderDestination | None: ...

    async def deliver(
        self,
        db: AsyncSession,
        destination: ProviderDestination,
        message: NotificationMessage,
    ) -> DeliveryResult: ...


def notification_message(
    notification: Notification, *, projection: str = "canonical", attempt_id: UUID | None = None
) -> NotificationMessage:
    public = projection in {"public_release", "media_shared"}
    return NotificationMessage(
        id=notification.id,
        kind=notification.kind,
        title=(notification.public_title or "Release announcement")
        if public
        else notification.title,
        body=(
            (notification.public_body or "Released")
            + (
                "\nSelected/followed by the destination owner."
                if projection == "media_shared"
                else ""
            )
        )
        if public
        else notification.body,
        media_type=notification.media_type,
        media_id=uuid5(NAMESPACE_URL, f"release:{notification.public_title}")
        if public
        else notification.media_id,
        event_at=notification.event_at,
        attempt_id=attempt_id,
    )
