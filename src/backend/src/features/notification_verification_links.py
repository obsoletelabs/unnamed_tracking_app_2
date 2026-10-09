"""Narrow email possession links reuse the existing challenge and verifier."""

import time
from typing import Literal
from urllib.parse import quote
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.crypto import decrypt_secret, encrypt_secret
from src.database.models.notification_destination import NotificationDestination
from src.database.models.notification_verification import NotificationVerification
from src.features.smtp_configuration import SMTP_PROVIDER

VERIFICATION_PATH = "/api/notifications/email-verify"


class VerificationToken(BaseModel):
    model_config = ConfigDict(extra="forbid")
    purpose: Literal["email.verification.v1"]
    owner: UUID
    destination: UUID
    revision: int = Field(ge=1)
    challenge: UUID
    code: str = Field(pattern=r"^[0-9]{8}$")
    expires: int


class VerificationLinkError(ValueError):
    """An unusable link, with no account or destination disclosure."""


def verification_link(origin: str, challenge: NotificationVerification) -> str:
    token = VerificationToken(
        purpose="email.verification.v1",
        owner=challenge.user_id,
        destination=challenge.destination_id,
        revision=challenge.destination_revision,
        challenge=challenge.id,
        code=decrypt_secret(challenge.encrypted_code or ""),
        expires=challenge.expires_at,
    )
    return f"{origin}{VERIFICATION_PATH}?token={quote(encrypt_secret(token.model_dump_json()), safe='')}"


async def verification_token(db: AsyncSession, opaque: str) -> VerificationToken:
    """Read-only validation for scanner-safe confirmation pages; POST rechecks under locks."""
    try:
        token = VerificationToken.model_validate_json(decrypt_secret(opaque))
        now = int(time.time())
        if not now < token.expires <= now + 600:
            raise ValueError("Expired proof")
        challenge = await db.scalar(
            select(NotificationVerification)
            .join(
                NotificationDestination,
                NotificationDestination.id == NotificationVerification.destination_id,
            )
            .where(
                NotificationVerification.id == token.challenge,
                NotificationVerification.user_id == token.owner,
                NotificationVerification.destination_id == token.destination,
                NotificationVerification.destination_revision == token.revision,
                NotificationVerification.expires_at == token.expires,
                NotificationVerification.used_at.is_(None),
                NotificationVerification.encrypted_code.is_not(None),
                NotificationDestination.user_id == token.owner,
                NotificationDestination.revision == token.revision,
                NotificationDestination.provider_id == SMTP_PROVIDER,
                NotificationDestination.active.is_(True),
                NotificationDestination.enabled.is_(True),
            )
        )
        if challenge is None:
            raise ValueError("Withdrawn proof")
        return token
    except (ValueError, RuntimeError) as exc:
        raise VerificationLinkError(
            "This verification link is invalid, expired or already used."
        ) from exc


async def confirm_verification_link(db: AsyncSession, opaque: str) -> None:
    # Enrollment loads the dispatcher/SMTP adapter; resolve the verifier after initialization.
    # pylint: disable-next=import-outside-toplevel
    from src.features.notification_enrollment import EnrollmentError, confirm_verification

    token = await verification_token(db, opaque)
    try:
        verified = await confirm_verification(
            db, token.owner, token.destination, token.challenge, token.code
        )
    except EnrollmentError as exc:
        raise VerificationLinkError("This verification link is invalid or has expired.") from exc
    if not verified:
        raise VerificationLinkError("This verification link is invalid, expired or already used.")
