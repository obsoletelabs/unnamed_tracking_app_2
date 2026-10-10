"""Email opt-out authority cannot read destinations, promote trust or reactivate delivery."""

import time
from dataclasses import dataclass
from typing import Literal, Self
from urllib.parse import quote
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.crypto import decrypt_secret, encrypt_secret
from src.core.preferences import load_preferences, save_preferences
from src.database.models.notification_destination import NotificationDestination
from src.database.models.user import User
from src.features.notification_lifecycle import (
    retire_destination_type_work,
    retire_destination_work,
)
from src.features.notification_policy import route_choice
from src.features.smtp_configuration import SMTP_PROVIDER

UNSUBSCRIBE_PATH = "/api/notifications/email-unsubscribe"
LINK_SECONDS = 90 * 24 * 60 * 60


class UnsubscribeToken(BaseModel):
    model_config = ConfigDict(extra="forbid")
    purpose: Literal["email.unsubscribe.v1", "email.unsubscribe.v2"]
    owner: UUID
    destination: UUID
    revision: int = Field(ge=1)
    address_key: str = Field(min_length=64, max_length=64)
    expires: int
    event_type: str | None = Field(
        default=None, min_length=3, max_length=128, pattern=r"^[a-z0-9][a-z0-9._-]*$"
    )

    @model_validator(mode="after")
    def validate_scope(self) -> Self:
        if (self.purpose == "email.unsubscribe.v2") != (self.event_type is not None):
            raise ValueError("Unsubscribe token scope does not match its version")
        return self


@dataclass(frozen=True)
class UnsubscribeStatus:
    event_type: str | None
    stopped: bool
    destination_disabled: bool


class UnsubscribeError(ValueError):
    """Invalid, expired or withdrawn opt-out authority, with no destination disclosure."""


def unsubscribe_link(origin: str, destination: NotificationDestination, event_type: str) -> str:
    token = UnsubscribeToken(
        purpose="email.unsubscribe.v2",
        owner=destination.user_id,
        destination=destination.id,
        revision=destination.revision,
        address_key=destination.endpoint_key,
        expires=int(time.time()) + LINK_SECONDS,
        event_type=event_type,
    )
    opaque = encrypt_secret(token.model_dump_json())
    return f"{origin}{UNSUBSCRIBE_PATH}?token={quote(opaque, safe='')}"


def _read_token(opaque: str) -> UnsubscribeToken:
    try:
        token = UnsubscribeToken.model_validate_json(decrypt_secret(opaque))
        now = int(time.time())
        if not now < token.expires <= now + LINK_SECONDS:
            raise ValueError("Expired token")
        return token
    except (ValueError, RuntimeError) as exc:
        raise UnsubscribeError("This unsubscribe link is invalid or has expired.") from exc


async def _destination(
    db: AsyncSession, token: UnsubscribeToken, *, lock: bool
) -> NotificationDestination:
    # Match the locking order used by authenticated enrollment and verification.
    if lock:
        await db.scalar(select(User.id).where(User.id == token.owner).with_for_update())
    query = select(NotificationDestination).where(
        NotificationDestination.id == token.destination,
        NotificationDestination.user_id == token.owner,
        NotificationDestination.provider_id == SMTP_PROVIDER,
        NotificationDestination.kind == "email",
        NotificationDestination.active.is_(True),
        NotificationDestination.endpoint_key == token.address_key,
    )
    row = await db.scalar(query.with_for_update() if lock else query)
    if row is None or (row.enabled and row.revision != token.revision):
        raise UnsubscribeError("This unsubscribe link is invalid or has expired.")
    return row


async def unsubscribe_status(db: AsyncSession, opaque: str) -> UnsubscribeStatus:
    """GET is read-only, including when a scanner opens the link."""
    token = _read_token(opaque)
    destination = await _destination(db, token, lock=False)
    preferences = await load_preferences(db, token.owner)
    stopped = not destination.enabled or (
        token.event_type is not None
        and not route_choice(token.event_type, str(destination.id), preferences)["enabled"]
    )
    return UnsubscribeStatus(token.event_type, stopped, not destination.enabled)


async def unsubscribe_email(
    db: AsyncSession, opaque: str, scope: Literal["type", "all"] | None = None
) -> str | None:
    token = _read_token(opaque)
    destination = await _destination(db, token, lock=True)
    if scope != "all" and token.event_type is not None:
        preferences = await load_preferences(db, token.owner, lock=True)
        routes = dict(preferences["notification_routes"])
        choices = dict(routes.get(token.event_type, {}))
        choice = route_choice(token.event_type, str(destination.id), preferences)
        choices[str(destination.id)] = {**choice, "enabled": False}
        routes[token.event_type] = choices
        try:
            await save_preferences(db, token.owner, {"notification_routes": routes}, commit=False)
        except ValueError as exc:
            raise UnsubscribeError("Manage notification routing after signing in.") from exc
        await retire_destination_type_work(db, destination.id, token.event_type)
        await db.commit()
        return token.event_type
    if scope == "type":
        raise UnsubscribeError("This older link can only stop all email types to this destination.")
    if destination.enabled:
        old_revision = destination.revision
        destination.enabled = False
        destination.revision += 1
        # Opt-out changes activation, not the address or evidence of possession.
        if destination.verified_revision == old_revision:
            destination.verified_revision = destination.revision
    await retire_destination_work(db, destination.id, "email_unsubscribed")
    await db.commit()
    return None
