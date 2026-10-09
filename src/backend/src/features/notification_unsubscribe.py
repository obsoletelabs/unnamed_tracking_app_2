"""Email opt-out authority cannot read destinations, promote trust or reactivate delivery."""

import time
from typing import Literal
from urllib.parse import quote
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.crypto import decrypt_secret, encrypt_secret
from src.database.models.notification_destination import NotificationDestination
from src.database.models.user import User
from src.features.notification_lifecycle import retire_destination_work
from src.features.smtp_configuration import SMTP_PROVIDER

UNSUBSCRIBE_PATH = "/api/notifications/email-unsubscribe"
LINK_SECONDS = 90 * 24 * 60 * 60


class UnsubscribeToken(BaseModel):
    model_config = ConfigDict(extra="forbid")
    purpose: Literal["email.unsubscribe.v1"]
    owner: UUID
    destination: UUID
    revision: int = Field(ge=1)
    address_key: str = Field(min_length=64, max_length=64)
    expires: int


class UnsubscribeError(ValueError):
    """Invalid, expired or withdrawn opt-out authority, with no destination disclosure."""


def unsubscribe_link(origin: str, destination: NotificationDestination) -> str:
    token = UnsubscribeToken(
        purpose="email.unsubscribe.v1",
        owner=destination.user_id,
        destination=destination.id,
        revision=destination.revision,
        address_key=destination.endpoint_key,
        expires=int(time.time()) + LINK_SECONDS,
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


async def unsubscribe_status(db: AsyncSession, opaque: str) -> bool:
    """GET is read-only, including when a scanner opens the link."""
    return not (await _destination(db, _read_token(opaque), lock=False)).enabled


async def unsubscribe_email(db: AsyncSession, opaque: str) -> None:
    destination = await _destination(db, _read_token(opaque), lock=True)
    if destination.enabled:
        old_revision = destination.revision
        destination.enabled = False
        destination.revision += 1
        # Opt-out changes activation, not the address or evidence of possession.
        if destination.verified_revision == old_revision:
            destination.verified_revision = destination.revision
    await retire_destination_work(db, destination.id, "email_unsubscribed")
    await db.commit()
