"""Administrator account management that reuses auth, recovery and verification flows.

Every action is audited without secrets. Sensitive actions require the acting
administrator's current password and the target username typed back. Reset
links and verification proofs are only ever delivered through the core
notification policy, so they can reach nothing but destinations that already
proved possession; an administrator can never force delivery to an unverified
address and can never read a password or token.
"""

import asyncio
import secrets
import time
from typing import NamedTuple
from uuid import UUID

from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.auth import hash_password, validate_password, verify_password
from src.core.config import settings
from src.database.models.admin_user_audit import AdminUserAudit
from src.database.models.auth import UserSession
from src.database.models.notification_destination import NotificationDestination
from src.database.models.plugin_auth_flow import AuthRecoveryToken
from src.database.models.user import User
from src.features.auth_rate_limits import allow_auth_request, allow_recovery_request
from src.features.notification_enrollment import (
    EnrollmentError,
    create_email,
    endpoint_address,
    request_verification,
    revoke_email,
)
from src.features.password_recovery import issue_reset_token
from src.features.plugin_auth_flows import active_reset_flow
from src.features.smtp_configuration import SMTP_PROVIDER, normalize_email


class AdminActionError(Exception):
    """A refused action; the message is safe to show to the administrator."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


class Actor(NamedTuple):
    """Plain snapshot of the acting administrator; stays readable after a rollback."""

    id: UUID
    username: str


def actor_of(admin: User | Actor) -> Actor:
    return Actor(admin.id, admin.username)


def is_primary_user(user: User) -> bool:
    return (
        user.username == settings.PRIMARY_USER_USERNAME
        or user.email == settings.PRIMARY_USER_EMAIL.lower()
    )


def mask_email(address: str) -> str:
    local, _, domain = address.partition("@")
    return f"{local[:1]}***@{domain}" if domain else "***"


def record_audit(
    db: AsyncSession,
    actor: User | Actor,
    action: str,
    *,
    outcome: str = "success",
    target_id: UUID | None = None,
    target_label: str = "",
    detail: str = "",
) -> None:
    """Stage an audit row in the caller's transaction (no secrets, bounded text)."""
    db.add(
        AdminUserAudit(
            actor_id=actor.id,
            actor_username=actor.username,
            target_id=target_id,
            target_label=target_label[:320],
            action=action,
            outcome=outcome,
            detail=detail[:512],
        )
    )


async def list_audit(db: AsyncSession, target_id: UUID, limit: int = 50) -> list[dict]:
    rows = await db.scalars(
        select(AdminUserAudit)
        .where(AdminUserAudit.target_id == target_id)
        .order_by(AdminUserAudit.occurred_at.desc(), AdminUserAudit.id)
        .limit(max(1, min(limit, 100)))
    )
    return [
        {
            "id": str(row.id),
            "actor": row.actor_username,
            "action": row.action,
            "outcome": row.outcome,
            "detail": row.detail,
            "occurred_at": row.occurred_at,
        }
        for row in rows
    ]


async def _locked_target(db: AsyncSession, user_id: UUID) -> User:
    target = await db.scalar(
        select(User)
        .where(User.id == user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if target is None:
        raise AdminActionError("User not found.", 404)
    return target


async def _deny(db, admin, action, target, message, status_code, *, detail=""):
    record_audit(
        db,
        admin,
        action,
        outcome="denied",
        target_id=target.id,
        target_label=target.username,
        detail=detail or message,
    )
    await db.commit()
    raise AdminActionError(message, status_code)


async def _authorize_sensitive(
    db: AsyncSession,
    admin: User,
    target: User,
    action: str,
    *,
    admin_password: str,
    confirm_username: str,
) -> None:
    """Explicit confirmation plus fresh proof of the acting administrator's identity."""
    if target.id == admin.id:
        raise AdminActionError("Use your own profile settings to change your credentials.", 400)
    if is_primary_user(target):
        await _deny(
            db,
            admin,
            action,
            target,
            "The primary user's credentials can only be changed by that account.",
            403,
        )
    if confirm_username != target.username:
        raise AdminActionError("Type the account's username exactly to confirm.", 400)
    allowed = await allow_auth_request(db, "admin-reauth", (("admin", str(admin.id), 10),))
    if not allowed:
        await _deny(db, admin, action, target, "Too many attempts; try again later.", 429)
    # Sessions alone are never enough proof for a credential-level change.
    current = await db.scalar(select(User.password_hash).where(User.id == admin.id))
    if not current or not await asyncio.to_thread(verify_password, admin_password, current):
        await _deny(
            db,
            admin,
            action,
            target,
            "Your administrator password is incorrect.",
            403,
            detail="Administrator password check failed",
        )


async def change_email(
    db: AsyncSession,
    admin: User,
    user_id: UUID,
    new_email: str,
    *,
    admin_password: str,
    confirm_username: str,
) -> dict:
    target = await _locked_target(db, user_id)
    await _authorize_sensitive(
        db,
        admin,
        target,
        "email.change",
        admin_password=admin_password,
        confirm_username=confirm_username,
    )
    try:
        email = normalize_email(new_email)
    except ValueError as exc:
        raise AdminActionError(str(exc), 400) from exc
    if email == target.email:
        raise AdminActionError("That is already this account's email address.", 400)
    clash = await db.scalar(
        select(User.id).where(
            User.id != target.id,
            or_(func.lower(User.email) == email, func.lower(User.username) == email),
        )
    )
    if clash is not None:
        raise AdminActionError("Another account already uses this email address.", 409)
    previous = target.email
    target.email = email
    record_audit(
        db,
        admin,
        "email.change",
        target_id=target.id,
        target_label=target.username,
        detail=f"{mask_email(previous)} -> {mask_email(email)}",
    )
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise AdminActionError("Another account already uses this email address.", 409) from exc
    # The account fingerprint covers the email, so outstanding reset links die with it.
    return {"status": "email_changed", "email": email}


async def _end_credentials(db: AsyncSession, user: User) -> int:
    """Sign every browser session out and void outstanding reset links."""
    now = int(time.time())
    result = await db.execute(
        update(UserSession)
        .where(UserSession.user_id == user.id, UserSession.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    await db.execute(
        update(AuthRecoveryToken)
        .where(AuthRecoveryToken.user_id == user.id, AuthRecoveryToken.used_at.is_(None))
        .values(used_at=now, encrypted_token=None)
    )
    return result.rowcount or 0


def _require_local_password(target: User) -> None:
    if target.oidc_subject is not None:
        raise AdminActionError(
            "This account signs in through single sign-on; it has no password.", 409
        )
    if not target.is_active:
        raise AdminActionError("This account is disabled.", 409)


async def set_password(
    db: AsyncSession,
    admin: User,
    user_id: UUID,
    new_password: str,
    *,
    admin_password: str,
    confirm_username: str,
) -> dict:
    target = await _locked_target(db, user_id)
    await _authorize_sensitive(
        db,
        admin,
        target,
        "password.set",
        admin_password=admin_password,
        confirm_username=confirm_username,
    )
    _require_local_password(target)
    try:
        validate_password(new_password)
    except ValueError as exc:
        raise AdminActionError(str(exc), 422) from exc
    target.password_hash = await asyncio.to_thread(hash_password, new_password)
    revoked = await _end_credentials(db, target)
    record_audit(
        db,
        admin,
        "password.set",
        target_id=target.id,
        target_label=target.username,
        detail=f"Password replaced; {revoked} session(s) signed out",
    )
    await db.commit()
    return {"status": "password_changed", "sessions_revoked": revoked}


async def _reset_flow_or_refuse(db: AsyncSession):
    flow = await active_reset_flow(db)
    if flow is None:
        raise AdminActionError("No password-reset plugin is active and granted.", 409)
    return flow


NO_RECOVERY_ROUTE = (
    "This user has no verified email destination with recovery delivery enabled, so no link "
    "was sent. Ask them to verify an address, or set a password instead."
)


async def send_reset_link(
    db: AsyncSession,
    admin: User,
    user_id: UUID,
    *,
    force: bool,
    admin_password: str,
    confirm_username: str,
) -> dict:
    """Queue a reset link; with force, the current password and sessions are voided first.

    Both modes deliver only through verified recovery destinations. Force is
    all-or-nothing: without an eligible destination nothing is changed.
    """
    action = "password.force_reset" if force else "password.reset_link"
    actor = actor_of(admin)
    target = await _locked_target(db, user_id)
    if force:
        await _authorize_sensitive(
            db,
            admin,
            target,
            action,
            admin_password=admin_password,
            confirm_username=confirm_username,
        )
    elif is_primary_user(target) and target.id != admin.id:
        await _deny(
            db,
            admin,
            action,
            target,
            "The primary user's credentials can only be changed by that account.",
            403,
        )
    _require_local_password(target)
    flow = await _reset_flow_or_refuse(db)
    if not await allow_recovery_request(db, target.email.lower(), f"admin:{admin.id}"):
        await _deny(db, admin, action, target, "Too many reset requests; try again later.", 429)
    label = target.username
    revoked = 0
    if force:
        # Replace the hash first: reset links are bound to the account fingerprint.
        target.password_hash = await asyncio.to_thread(hash_password, secrets.token_urlsafe(48))
        revoked = await _end_credentials(db, target)
    if not await issue_reset_token(db, target, flow):
        await db.rollback()
        record_audit(
            db,
            actor,
            action,
            outcome="refused",
            target_id=user_id,
            target_label=label,
            detail="No verified recovery destination; nothing changed",
        )
        await db.commit()
        raise AdminActionError(NO_RECOVERY_ROUTE, 409)
    record_audit(
        db,
        admin,
        action,
        target_id=target.id,
        target_label=target.username,
        detail="Reset link queued for verified recovery destinations"
        + (f"; password voided, {revoked} session(s) signed out" if force else ""),
    )
    await db.commit()
    return {"status": "reset_link_queued", "password_voided": force, "sessions_revoked": revoked}


def _destination_view(destination: NotificationDestination, account_email: str) -> dict:
    address = endpoint_address(destination)
    verified = (
        destination.verified_revision == destination.revision
        and destination.verification_revoked_at is None
    )
    return {
        "id": str(destination.id),
        "address": mask_email(address),
        "is_account_email": address.lower() == account_email.lower(),
        "verified": verified,
        "recovery_allowed": destination.recovery_allowed,
        "enabled": destination.enabled,
    }


async def _email_destinations(db: AsyncSession, user_id: UUID) -> list[NotificationDestination]:
    return list(
        await db.scalars(
            select(NotificationDestination)
            .where(
                NotificationDestination.user_id == user_id,
                NotificationDestination.provider_id == SMTP_PROVIDER,
                NotificationDestination.active.is_(True),
                NotificationDestination.encrypted_configuration.is_not(None),
            )
            .order_by(NotificationDestination.created_at)
        )
    )


async def list_email_destinations(db: AsyncSession, user_id: UUID) -> list[dict]:
    target = await db.get(User, user_id)
    if target is None:
        raise AdminActionError("User not found.", 404)
    return [_destination_view(row, target.email) for row in await _email_destinations(db, user_id)]


async def send_verification(
    db: AsyncSession, admin: User, user_id: UUID, destination_id: UUID | None
) -> dict:
    """Ask the destination's own mailbox to prove possession (nothing is marked verified here).

    Without a destination, the account email is enrolled as an unverified
    destination first; the user (or whoever controls the mailbox) completes the
    existing code or link confirmation.
    """
    target = await db.get(User, user_id)
    if target is None:
        raise AdminActionError("User not found.", 404)
    label = target.username
    actor = actor_of(admin)
    try:
        if destination_id is None:
            existing = next(
                (
                    row
                    for row in await _email_destinations(db, user_id)
                    if endpoint_address(row).lower() == target.email.lower()
                ),
                None,
            )
            destination_id = (
                existing.id
                if existing
                else await create_email(db, user_id, target.email, "Account email")
            )
        await request_verification(db, user_id, destination_id)
    except (EnrollmentError, ValueError) as exc:
        await db.rollback()
        record_audit(
            db,
            actor,
            "email.verification_request",
            outcome="refused",
            target_id=user_id,
            target_label=label,
            detail=str(exc),
        )
        await db.commit()
        raise AdminActionError(str(exc), getattr(exc, "status_code", 400)) from exc
    record_audit(
        db,
        admin,
        "email.verification_request",
        target_id=user_id,
        target_label=label,
        detail="Verification message queued to the destination's own mailbox",
    )
    await db.commit()
    return {"status": "verification_sent"}


async def revoke_verification(
    db: AsyncSession, admin: User, user_id: UUID, destination_id: UUID, confirm_username: str
) -> dict:
    """Withdraw proof (and recovery delivery) for a destination; it must be verified again."""
    target = await db.get(User, user_id)
    if target is None:
        raise AdminActionError("User not found.", 404)
    label = target.username
    if confirm_username != label:
        raise AdminActionError("Type the account's username exactly to confirm.", 400)
    try:
        await revoke_email(db, user_id, destination_id)
    except EnrollmentError as exc:
        raise AdminActionError(str(exc), exc.status_code) from exc
    record_audit(
        db,
        admin,
        "email.verification_revoke",
        target_id=user_id,
        target_label=label,
        detail="Verification and recovery delivery withdrawn",
    )
    await db.commit()
    return {"status": "verification_revoked"}
