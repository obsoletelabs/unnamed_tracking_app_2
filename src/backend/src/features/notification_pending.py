"""Host-only invitation bootstrap policy; pending identities are never normal user routes."""

import hmac
import json
import time
from dataclasses import replace
from urllib.parse import urlencode

from sqlalchemy import select

from src.core.auth import hash_token
from src.core.config import settings
from src.core.crypto import decrypt_secret
from src.database.models.user import User
from src.database.models.user_invitation import NotificationPendingRecipient, UserInvitation
from src.features.notification_providers.base import EmailPresentation
from src.features.plugin_auth_flows import active_auth_flow
from src.features.smtp_configuration import SMTP_PROVIDER
from src.plugin_api.auth_contracts import UserInviteFlowDefinition


async def active_invitation(db, notice):
    if (
        notice.user_id is not None
        or notice.pending_recipient_id is None
        or notice.event_type != "auth.user_invite"
        or notice.purpose != "invitation"
        or notice.deleted_at is not None
    ):
        return None
    row = await db.scalar(
        select(UserInvitation)
        .where(
            UserInvitation.id == notice.media_id,
            UserInvitation.notification_id == notice.id,
            UserInvitation.pending_recipient_id == notice.pending_recipient_id,
        )
        .execution_options(populate_existing=True)
    )
    return row if await invitation_is_active(db, row) else None


async def invitation_is_active(db, row):
    if row is None:
        return False
    flow = await active_auth_flow(db, "user_invite")
    pending = await db.get(
        NotificationPendingRecipient, row.pending_recipient_id, populate_existing=True
    )
    issuer = await db.get(User, row.issued_by, populate_existing=True)
    return bool(
        flow
        and pending
        and issuer
        and issuer.is_active
        and issuer.is_admin
        and pending.retired_at is None
        and pending.expires_at > int(time.time())
        and row.used_at is None
        and row.revoked_at is None
        and row.expires_at > int(time.time())
        and row.encrypted_token
        and row.plugin_id == flow.plugin_id
        and row.installation_id == flow.installation_id
        and row.flow_revision == flow.revision
    )


async def pending_projection(db, notice, endpoint):
    row = await active_invitation(db, notice)
    if row is None or endpoint is None:
        return None
    blocked_providers = {
        value.strip() for value in settings.NOTIFICATION_BLOCKED_PROVIDERS.split(",")
    }
    blocked_types = {value.strip() for value in settings.NOTIFICATION_BLOCKED_TYPES.split(",")}
    if not all(
        (
            endpoint.user_id is None,
            endpoint.pending_recipient_id == row.pending_recipient_id,
            endpoint.provider_id == SMTP_PROVIDER,
            endpoint.endpoint_key == str(row.id),
            endpoint.kind == "invitation_email",
            endpoint.channel_context == "external",
            endpoint.privacy == 1,
            endpoint.verified_revision is None,
            endpoint.installation_id is None,
            endpoint.active and endpoint.enabled,
            notice.source == row.plugin_id,
            notice.source_installation_id == row.installation_id,
            notice.required_trust == 1,
            settings.NOTIFICATION_MINIMUM_TRUST <= 1,
            SMTP_PROVIDER not in blocked_providers,
            notice.event_type not in blocked_types,
        )
    ):
        return None
    return "canonical"


async def invitation_message(db, notice, endpoint, message):
    if notice.event_type != "auth.user_invite":
        return message
    row = await active_invitation(db, notice)
    if row is None or await pending_projection(db, notice, endpoint) is None:
        return None
    link = (
        row.app_url.rstrip("/")
        + "/accept-invite#"
        + urlencode({"id": str(row.id), "token": decrypt_secret(row.encrypted_token)})
    )
    # Snapshot presentation with the encrypted recipient endpoint at issuance.
    # Later cosmetic changes do not rewrite an already queued invitation.
    config = json.loads(decrypt_secret(endpoint.encrypted_configuration))
    definition = UserInviteFlowDefinition.model_validate(
        config.get("invitation") or {"mode": "user_invite", "name": "User Invites"}
    )
    recipient_name = config.get("recipient_name", "").strip()
    greeting = (
        f"Hi {recipient_name},"
        if definition.greeting == "personal" and recipient_name
        else "Hello,"
        if definition.greeting != "none"
        else ""
    )
    return replace(
        message,
        action_url=link,
        title=definition.email_subject,
        body="\n\n".join(
            part
            for part in (
                greeting,
                definition.welcome_message,
                "Open this link to choose a username and password:\n" + link,
                "This link expires and can be used once. If you did not expect it, ignore this email.",
            )
            if part
        ),
        email_presentation=EmailPresentation(
            sender_name=definition.sender_name,
            heading=definition.email_heading,
            action_label=definition.button_label,
            accent_color=definition.accent_color,
            layout=definition.email_layout,
            footer=definition.footer_message,
        ),
    )


def matches_invitation_token(row, token):
    return hmac.compare_digest(row.token_hash, hash_token(token))
