"""Host-mediated reset credentials, core notification routing and atomic redemption."""

import asyncio
import hmac
import secrets
import time
from uuid import UUID, uuid4

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.auth import hash_password, hash_token, validate_password
from src.core.crypto import encrypt_secret
from src.database.models.auth import UserSession
from src.database.models.notification import Notification
from src.database.models.notification_delivery import NotificationDelivery
from src.database.models.plugin_auth_flow import AuthRecoveryToken
from src.database.models.user import User
from src.features.auth_rate_limits import allow_recovery_request
from src.features.notification_auth_secrets import account_fingerprint, recovery_token_matches
from src.features.notification_controller import emit_recovery_request
from src.features.plugin_auth_flows import active_reset_flow
from src.plugin_api.auth_contracts import PasswordResetFlowDefinition


async def request_password_reset(db: AsyncSession, identifier: str, caller: str) -> None:
    """Unknown, disabled, SSO and ineligible accounts all receive the same acknowledgement."""
    normalized = identifier.strip().lower()
    permitted = await allow_recovery_request(db, normalized, caller)
    flow = await active_reset_flow(db) if permitted else None
    if flow is None:
        await db.commit()
        return
    users = list(
        await db.scalars(
            select(User)
            .where(
                or_(func.lower(User.email) == normalized, func.lower(User.username) == normalized),
                User.is_active.is_(True),
                User.oidc_subject.is_(None),
            )
            .with_for_update()
        )
    )
    # Ambiguous cross-field matches must not select an arbitrary account.
    if len(users) != 1:
        await db.commit()
        return
    user = users[0]
    token = secrets.token_urlsafe(32)
    record = AuthRecoveryToken(
        id=uuid4(),
        user_id=user.id,
        plugin_id=flow.plugin_id,
        installation_id=flow.installation_id,
        flow_revision=flow.revision,
        token_hash=hash_token(token),
        encrypted_token=encrypt_secret(token),
        account_fingerprint=account_fingerprint(user),
        expires_at=int(time.time())
        + PasswordResetFlowDefinition.model_validate(flow.definition).token_lifetime_seconds,
    )
    db.add(record)
    await db.flush()
    record.notification_id = await emit_recovery_request(db, record)
    delivery = await db.scalar(
        select(NotificationDelivery.id)
        .where(NotificationDelivery.notification_id == record.notification_id)
        .limit(1)
    )
    if delivery is None:
        # No usable destination: retain limits but not an unusable credential or notice.
        if record.notification_id:
            await db.execute(delete(Notification).where(Notification.id == record.notification_id))
        await db.delete(record)
    await db.commit()


async def complete_password_reset(
    db: AsyncSession, token_id: UUID, token: str, password: str
) -> bool:
    validate_password(password)
    hint = await db.get(AuthRecoveryToken, token_id)
    if hint is None:
        return False
    # All redemptions for an account lock in the same order, including different tokens.
    user = await db.scalar(
        select(User)
        .where(User.id == hint.user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    record = await db.scalar(
        select(AuthRecoveryToken)
        .where(AuthRecoveryToken.id == token_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    flow = await active_reset_flow(db)
    if user is None or record is None:
        return False
    if not recovery_token_matches(record, user, flow) or not hmac.compare_digest(
        record.token_hash, hash_token(token)
    ):
        return False
    user.password_hash = await asyncio.to_thread(hash_password, password)
    now = int(time.time())
    await db.execute(
        update(AuthRecoveryToken)
        .where(AuthRecoveryToken.user_id == user.id, AuthRecoveryToken.used_at.is_(None))
        .values(used_at=now, encrypted_token=None)
    )
    await db.execute(
        update(UserSession)
        .where(UserSession.user_id == user.id, UserSession.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    # UserApiKey is intentionally untouched: this is the approved reset policy.
    await db.commit()
    return True


async def expire_recovery_secrets(db: AsyncSession) -> None:
    await db.execute(
        update(AuthRecoveryToken)
        .where(
            AuthRecoveryToken.expires_at <= int(time.time()),
            AuthRecoveryToken.encrypted_token.is_not(None),
        )
        .values(encrypted_token=None)
    )
    await db.execute(
        delete(AuthRecoveryToken).where(AuthRecoveryToken.expires_at < int(time.time()) - 86400)
    )
