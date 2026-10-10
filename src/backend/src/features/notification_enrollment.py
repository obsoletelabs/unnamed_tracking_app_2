"""Owned email enrollment and revision-bound challenges; no provider controls verification."""

import hashlib
import hmac
import json
import secrets
import time
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.functions import count as sql_count

from src.core.config import settings
from src.core.crypto import decrypt_secret, encrypt_secret
from src.database.models.notification_destination import NotificationDestination
from src.database.models.notification_provider_setting import NotificationProviderSetting
from src.database.models.notification_verification import NotificationVerification
from src.database.models.user import User
from src.features.notification_controller import emit_verification_request
from src.features.notification_lifecycle import invalidate_endpoint
from src.features.notification_policy import Trust
from src.features.smtp_configuration import SMTP_PROVIDER, normalize_email, smtp_configuration

CHALLENGE_SECONDS = 600
MAX_FAILURES = 5
RESEND_SECONDS = 60
MAX_REQUESTS_PER_HOUR = 10


class EnrollmentError(ValueError):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


async def _lock_owner(db: AsyncSession, user_id: UUID) -> None:
    await db.scalar(select(User.id).where(User.id == user_id).with_for_update())


async def owned_destination(
    db: AsyncSession, user_id: UUID, destination_id: UUID, *, provider_id: str = SMTP_PROVIDER
) -> NotificationDestination:
    row = await db.scalar(
        select(NotificationDestination)
        .where(
            NotificationDestination.id == destination_id,
            NotificationDestination.user_id == user_id,
            NotificationDestination.provider_id == provider_id,
            NotificationDestination.active.is_(True),
        )
        .with_for_update()
    )
    if row is None:
        raise EnrollmentError("Notification destination not found", 404)
    return row


def endpoint_address(destination: NotificationDestination) -> str:
    if not destination.encrypted_configuration:
        raise EnrollmentError("Email destination is not configured")
    return json.loads(decrypt_secret(destination.encrypted_configuration))["address"]


def _address_key(address: str) -> str:
    return hmac.new(settings.SECRET_KEY.encode(), address.encode(), hashlib.sha256).hexdigest()


async def enforce_destination_limit(db: AsyncSession, user_id: UUID, provider_id: str) -> None:
    count = await db.scalar(
        select(sql_count())
        .select_from(NotificationDestination)
        .where(
            NotificationDestination.user_id == user_id,
            NotificationDestination.active.is_(True),
            NotificationDestination.provider_id == provider_id,
        )
    )
    if count and count >= 20:
        raise EnrollmentError("At most 20 active destinations per provider are allowed")


async def create_email(db: AsyncSession, user_id: UUID, address: str, label: str) -> UUID:
    address = normalize_email(address)
    await _lock_owner(db, user_id)
    await enforce_destination_limit(db, user_id, SMTP_PROVIDER)
    existing = await db.scalar(
        select(NotificationDestination.id).where(
            NotificationDestination.user_id == user_id,
            NotificationDestination.provider_id == SMTP_PROVIDER,
            NotificationDestination.endpoint_key == _address_key(address),
        )
    )
    if existing:
        raise EnrollmentError(
            "This address already has a destination; edit the existing entry", 409
        )
    row = NotificationDestination(
        user_id=user_id,
        provider_id=SMTP_PROVIDER,
        endpoint_key=_address_key(address),
        kind="email",
        channel_context="external",
        privacy=int(Trust.PRIVATE),
        display_name=label.strip() or "Email",
        encrypted_configuration=encrypt_secret(json.dumps({"address": address})),
    )
    db.add(row)
    setting = await db.scalar(
        select(NotificationProviderSetting).where(
            NotificationProviderSetting.user_id == user_id,
            NotificationProviderSetting.provider_id == SMTP_PROVIDER,
        )
    )
    if setting is None:
        db.add(
            NotificationProviderSetting(user_id=user_id, provider_id=SMTP_PROVIDER, enabled=True)
        )
    await db.commit()
    return row.id


async def update_email(
    db: AsyncSession, user_id: UUID, destination_id: UUID, changes: dict
) -> None:
    await _lock_owner(db, user_id)
    destination = await owned_destination(db, user_id, destination_id)
    if changes.get("address") is not None:
        address = normalize_email(changes["address"])
        if address != endpoint_address(destination):
            key = _address_key(address)
            duplicate = await db.scalar(
                select(NotificationDestination.id).where(
                    NotificationDestination.user_id == user_id,
                    NotificationDestination.provider_id == SMTP_PROVIDER,
                    NotificationDestination.endpoint_key == key,
                    NotificationDestination.id != destination.id,
                )
            )
            if duplicate:
                raise EnrollmentError("This address already has a destination", 409)
            await invalidate_endpoint(db, destination)
            destination.endpoint_key = key
            destination.encrypted_configuration = encrypt_secret(json.dumps({"address": address}))
    if "label" in changes:
        destination.display_name = changes["label"].strip() or "Email"
    if "enabled" in changes:
        destination.enabled = changes["enabled"]
    if "recovery_allowed" in changes:
        if changes["recovery_allowed"] and (
            destination.verified_revision != destination.revision
            or destination.verification_revoked_at is not None
        ):
            raise EnrollmentError("Verify this email before enabling recovery-purpose delivery")
        destination.recovery_allowed = changes["recovery_allowed"]
    await db.commit()


async def revoke_email(
    db: AsyncSession, user_id: UUID, destination_id: UUID, *, remove: bool = False
) -> None:
    await _lock_owner(db, user_id)
    destination = await owned_destination(db, user_id, destination_id)
    await invalidate_endpoint(db, destination)
    if remove:
        destination.active = False
        destination.enabled = False
        destination.encrypted_configuration = None
        # Retain a non-address key so a fresh enrollment cannot reclaim the old identity.
        destination.endpoint_key = f"retired:{destination.id}"
    await db.commit()


def _digest(challenge: NotificationVerification, code: str) -> str:
    identity = f"{challenge.id}:{challenge.user_id}:{challenge.destination_id}:{challenge.destination_revision}:{code}"
    return hmac.new(settings.SECRET_KEY.encode(), identity.encode(), hashlib.sha256).hexdigest()


async def request_verification(db: AsyncSession, user_id: UUID, destination_id: UUID) -> UUID:
    await _lock_owner(db, user_id)
    destination = await owned_destination(db, user_id, destination_id)
    config = await smtp_configuration(db)
    if not config.configured or not config.allows_sensitive:
        raise EnrollmentError(
            "Verification requires configured SMTP and an eligible secure transport"
        )
    now = int(time.time())
    recent = list(
        await db.scalars(
            select(NotificationVerification).where(
                NotificationVerification.user_id == user_id,
                NotificationVerification.created_at > now - 3600,
            )
        )
    )
    if len(recent) >= MAX_REQUESTS_PER_HOUR or any(
        row.destination_id == destination_id and row.created_at > now - RESEND_SECONDS
        for row in recent
    ):
        raise EnrollmentError("Verification requests are limited; try again later", 429)
    setting = await db.scalar(
        select(NotificationProviderSetting).where(
            NotificationProviderSetting.user_id == user_id,
            NotificationProviderSetting.provider_id == SMTP_PROVIDER,
        )
    )
    if not setting or not setting.enabled:
        raise EnrollmentError("Enable this destination and its routing before requesting a code")
    await db.execute(
        update(NotificationVerification)
        .where(
            NotificationVerification.destination_id == destination_id,
            NotificationVerification.used_at.is_(None),
        )
        .values(used_at=now, encrypted_code=None)
    )
    challenge = NotificationVerification(
        id=uuid4(),
        user_id=user_id,
        destination_id=destination_id,
        destination_revision=destination.revision,
        expires_at=now + CHALLENGE_SECONDS,
        code_digest="",
        created_at=now,
    )
    code = f"{secrets.randbelow(100_000_000):08d}"
    challenge.code_digest = _digest(challenge, code)
    challenge.encrypted_code = encrypt_secret(code)
    challenge.notification_id = await emit_verification_request(
        db, destination, challenge.id, challenge.expires_at
    )
    if challenge.notification_id is None:
        raise EnrollmentError("Enable this destination and its routing before requesting a code")
    db.add(challenge)
    await db.commit()
    return challenge.id


async def confirm_verification(
    db: AsyncSession, user_id: UUID, destination_id: UUID, challenge_id: UUID, code: str
) -> bool:
    await _lock_owner(db, user_id)
    destination = await owned_destination(db, user_id, destination_id)
    challenge = await db.scalar(
        select(NotificationVerification)
        .where(
            NotificationVerification.id == challenge_id,
            NotificationVerification.user_id == user_id,
            NotificationVerification.destination_id == destination_id,
        )
        .with_for_update()
    )
    now = int(time.time())
    if challenge is None:
        return False
    if (
        challenge.used_at is not None
        or challenge.expires_at <= now
        or challenge.destination_revision != destination.revision
    ):
        challenge.encrypted_code = None
        await db.commit()
        return False
    challenge.failures += 1
    valid = hmac.compare_digest(challenge.code_digest, _digest(challenge, code))
    if valid and challenge.failures <= MAX_FAILURES:
        destination.verified_revision = destination.revision
        destination.verification_method = "email.code.v1"
        destination.verification_revoked_at = None
        challenge.used_at = now
        challenge.encrypted_code = None
    elif challenge.failures >= MAX_FAILURES:
        challenge.used_at = now
        challenge.encrypted_code = None
    await db.commit()
    return valid and challenge.failures <= MAX_FAILURES


async def expire_verification_secrets(db: AsyncSession) -> None:
    await db.execute(
        update(NotificationVerification)
        .where(
            NotificationVerification.expires_at <= int(time.time()),
            NotificationVerification.encrypted_code.is_not(None),
        )
        .values(encrypted_code=None)
    )
