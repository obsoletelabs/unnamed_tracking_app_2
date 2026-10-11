"""Resolve recovery secrets only after core routing, never into canonical content."""

import hashlib
import hmac
import time
from dataclasses import replace
from urllib.parse import urlencode

from sqlalchemy import select

from src.core.crypto import decrypt_secret
from src.database.models.notification import Notification
from src.database.models.plugin_auth_flow import AuthRecoveryToken
from src.database.models.user import User
from src.features.notification_providers.base import NotificationMessage
from src.features.notification_urls import notification_url
from src.features.plugin_auth_flows import active_reset_flow


def account_fingerprint(user: User) -> str:
    return hashlib.sha256(
        f"{user.email}/{user.password_hash}/{user.oidc_subject or ''}".encode()
    ).hexdigest()


def recovery_token_matches(token, user, flow) -> bool:
    """Redemption and final handoff share account, lifetime and installation authority."""
    if token is None or user is None or flow is None:
        return False
    return all(
        (
            user.is_active,
            user.oidc_subject is None,
            token.user_id == user.id,
            token.used_at is None,
            token.expires_at > int(time.time()),
            bool(token.encrypted_token),
            token.plugin_id == flow.plugin_id,
            token.installation_id == flow.installation_id,
            token.flow_revision == flow.revision,
            hmac.compare_digest(token.account_fingerprint, account_fingerprint(user)),
        )
    )


async def active_recovery_token(db, notice: Notification) -> AuthRecoveryToken | None:
    flow = await active_reset_flow(db)
    token = await db.scalar(
        select(AuthRecoveryToken)
        .where(
            AuthRecoveryToken.id == notice.media_id,
            AuthRecoveryToken.notification_id == notice.id,
            AuthRecoveryToken.user_id == notice.user_id,
            AuthRecoveryToken.expires_at > int(time.time()),
            AuthRecoveryToken.used_at.is_(None),
            AuthRecoveryToken.encrypted_token.is_not(None),
        )
        .execution_options(populate_existing=True)
    )
    user = await db.get(User, notice.user_id, populate_existing=True)
    if flow is None or not recovery_token_matches(token, user, flow):
        return None
    if notice.source != flow.plugin_id or notice.source_installation_id != flow.installation_id:
        return None
    return token


async def recovery_message(db, notice, endpoint, message: NotificationMessage):
    if notice.event_type != "auth.password_reset":
        return message
    token = await active_recovery_token(db, notice)
    origin = await notification_url(db, notice.user_id, endpoint)
    if token is None or not token.encrypted_token or not origin:
        return None
    link = (
        origin.rstrip("/")
        + "/reset-password#"
        + urlencode({"id": str(token.id), "token": decrypt_secret(token.encrypted_token)})
    )
    return replace(
        message,
        action_url=link,
        body=(
            "Open this link to choose a new password:\n"
            + link
            + "\nThis link expires shortly and can be used once. "
            + "If you did not request it, ignore this message."
        ),
    )
