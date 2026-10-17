"""Notification-provider contracts owned by the core application."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models.notification import Notification
from src.database.models.notification_destination import NotificationDestination
from src.database.models.notification_provider_setting import NotificationProviderSetting
from src.database.models.user import User


@dataclass(frozen=True)
class EmailPresentation:
    """Host-validated invitation appearance, never arbitrary HTML or sender addresses."""

    sender_name: str = ""
    heading: str = ""
    action_label: str = "Accept invitation"
    accent_color: str = "#3458d4"
    layout: str = "card"
    footer: str = "Sent by your tracking app."


@dataclass(frozen=True)
class DigestItem:  # pylint: disable=too-many-instance-attributes
    """One member of a composed digest; keeps per-item identity next to the summary text."""

    id: UUID
    kind: str
    event_type: str
    media_type: str
    media_id: UUID
    event_at: int
    title: str
    body: str
    # Link to the signed-in story page; only set for providers that can carry links.
    url: str | None = None


@dataclass(frozen=True)
class MessageAction:
    """A button the provider declared support for; always a plain, host-checked link."""

    label: str
    url: str


@dataclass(frozen=True)
class NotificationMessage:  # pylint: disable=too-many-instance-attributes
    id: UUID
    kind: str
    title: str
    body: str
    media_type: str
    media_id: UUID
    event_at: int
    attempt_id: UUID | None = None
    urgency: str = "normal"
    purpose: str = "standard"
    event_type: str = ""
    action_url: str | None = None
    email_presentation: EmailPresentation | None = None
    # Non-empty for a composed digest chunk; title/body already summarise these members.
    items: tuple[DigestItem, ...] = ()
    batch_id: UUID | None = None
    # Capability-negotiated presentation (only populated for providers that declared support).
    html: str | None = None
    actions: tuple[MessageAction, ...] = ()
    images: tuple[str, ...] = ()
    link: str | None = None
    # send | edit | follow_up. `remote_ref` is the provider's own opaque reference for the earlier
    # message: set for an edit, and for a follow-up only when the provider threads replies.
    operation: str = "send"
    remote_ref: str | None = None
    revision: int = 0
    # Stable across retries of one obligation, so a provider can deduplicate a repeated send.
    idempotency_key: str | None = None


@dataclass(frozen=True)
class ProviderDestination:
    user_id: UUID | None
    display: str
    allows_sensitive: bool = True
    endpoint_id: UUID | None = None
    endpoint_revision: int | None = None
    pending_recipient_id: UUID | None = None

    @classmethod
    def for_endpoint(
        cls, endpoint: NotificationDestination, display: str, *, allows_sensitive: bool = False
    ):
        """Bind a selected endpoint's owner and revision without copying credentials."""
        return cls(
            user_id=endpoint.user_id,
            display=endpoint.display_name or display,
            allows_sensitive=allows_sensitive,
            endpoint_id=endpoint.id,
            endpoint_revision=endpoint.revision,
        )


@dataclass(frozen=True)
class DeliveryResult:
    success: bool
    retryable: bool = False
    error: str | None = None
    # Opaque identifier of the remote message created or edited; stored only by the host.
    remote_ref: str | None = None
    # The earlier remote message is gone or can no longer be edited (an edit was refused).
    gone: bool = False


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

    # Optional capabilities. A provider declares `capabilities` (NotificationProviderCapabilities)
    # and, when it declares message updates, implements
    # `async def edit(db, destination, message) -> DeliveryResult`; the host never trusts a
    # declaration the adapter cannot back.


def notification_message(
    notification: Notification,
    *,
    projection: str = "canonical",
    attempt_id: UUID | None = None,
    urgency: str = "normal",
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
        urgency=urgency,
        purpose=notification.purpose,
        event_type=notification.event_type,
    )
