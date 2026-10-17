"""Administrator-issued email invitations; accounts exist only after atomic acceptance."""

import asyncio
import hashlib
import hmac
import json
import secrets
import time
from uuid import uuid4

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.exc import IntegrityError

from src.core.app_integrations import get_public_app_url
from src.core.auth import hash_password, hash_token, validate_password, validate_username
from src.core.config import settings
from src.core.crypto import decrypt_secret, encrypt_secret
from src.core.public_url import normalize_public_url
from src.database.models.notification_delivery import ACTIVE_DELIVERY_STATUSES, NotificationDelivery
from src.database.models.notification_destination import NotificationDestination
from src.database.models.user import User
from src.database.models.user_invitation import NotificationPendingRecipient, UserInvitation
from src.features.auth_rate_limits import allow_auth_request
from src.features.notification_audit import change_deliveries
from src.features.notification_controller import emit_invitation
from src.features.notification_pending import invitation_is_active, matches_invitation_token
from src.features.plugin_auth_flows import active_auth_flow
from src.features.smtp_configuration import SMTP_PROVIDER, normalize_email, smtp_configuration
from src.plugin_api.auth_contracts import (
    UserInviteFlowDefinition,
    UserInviteIdentity,
    UserInviteIssue,
)


async def _owned_flow(db, plugin_id, installation_id, user_id):
    admin = await db.get(User, user_id, populate_existing=True)
    flow = await active_auth_flow(db, "user_invite")
    if admin is None or not admin.is_active or not admin.is_admin:
        raise PermissionError("Invitations require an administrator")
    if flow is None or flow.plugin_id != plugin_id or flow.installation_id != installation_id:
        raise PermissionError(
            "Invitations require an administrator and this active granted installation"
        )
    return flow, admin


async def issue_invitation(db, plugin_id, installation_id, user_id, payload):
    invitation = UserInviteIssue.model_validate(payload)
    email = normalize_email(invitation.email)
    # Serialize issuance/configuration and the pending cap across host workers.
    await db.execute(select(func.pg_advisory_xact_lock(731856417203)))
    flow, admin = await _owned_flow(db, plugin_id, installation_id, user_id)
    config = await smtp_configuration(db)
    if not config.configured or not config.allows_sensitive:
        raise ValueError("Invitations require configured SMTP with an allowed secure transport")
    origin = normalize_public_url((await get_public_app_url(db)) or admin.last_app_url or "")
    if not origin:
        raise ValueError("Configure an app URL or sign in through the intended app address first")
    exists = await db.scalar(
        select(User.id).where(
            or_(
                func.lower(User.email) == email,
                func.lower(User.username) == email,
            )
        )
    )
    if exists is not None:
        raise ValueError("An account already uses this email address")
    allowed = await allow_auth_request(
        db,
        "invite",
        (
            ("issuer", str(admin.id), 100),
            ("recipient", email, 5),
        ),
        interval=86400,
    )
    if not allowed:
        await db.commit()
        raise ValueError("Invitation request limit reached; try again later")
    # SQLAlchemy builds this aggregate dynamically; it is not a Python callable.
    # pylint: disable-next=not-callable
    count = await db.scalar(select(func.count()).select_from(NotificationPendingRecipient))
    if count is not None and count >= 1000:
        await db.commit()
        raise ValueError("Pending invitation capacity reached; allow cleanup before issuing more")
    return await _create_invitation(
        db, flow, admin, email, origin, recipient_name=invitation.recipient_name
    )


async def _create_invitation(db, flow, admin, email, origin, *, recipient_name=""):
    digest = hmac.new(settings.SECRET_KEY.encode(), email.encode(), hashlib.sha256).hexdigest()
    # A new link replaces older outstanding invitations to exactly this address.
    older = list(
        await db.scalars(
            select(UserInvitation)
            .where(
                UserInvitation.email_digest == digest,
                UserInvitation.used_at.is_(None),
                UserInvitation.revoked_at.is_(None),
                UserInvitation.expires_at > int(time.time()),
                UserInvitation.encrypted_token.is_not(None),
            )
            .with_for_update()
        )
    )
    for previous in older:
        await _retire(db, previous, revoked=True)
    now = int(time.time())
    expires = now + UserInviteFlowDefinition.model_validate(flow.definition).token_lifetime_seconds
    recipient = NotificationPendingRecipient(id=uuid4(), expires_at=expires)
    db.add(recipient)
    await db.flush()
    token = secrets.token_urlsafe(32)
    row = UserInvitation(
        id=uuid4(),
        pending_recipient_id=recipient.id,
        issued_by=admin.id,
        plugin_id=flow.plugin_id,
        installation_id=flow.installation_id,
        flow_revision=flow.revision,
        email_digest=digest,
        encrypted_email=encrypt_secret(email),
        token_hash=hash_token(token),
        encrypted_token=encrypt_secret(token),
        app_url=origin,
        expires_at=expires,
    )
    endpoint = NotificationDestination(
        id=uuid4(),
        pending_recipient_id=recipient.id,
        provider_id=SMTP_PROVIDER,
        endpoint_key=str(row.id),
        kind="invitation_email",
        privacy=1,
        encrypted_configuration=encrypt_secret(
            json.dumps(
                {"address": email, "recipient_name": recipient_name, "invitation": flow.definition}
            )
        ),
        notification_url=origin,
        display_name="Invitation email",
    )
    db.add_all([row, endpoint])
    await db.flush()
    row.notification_id = await emit_invitation(db, row, endpoint)
    await db.commit()
    return {"id": str(row.id), "expires_at": expires, "status": "pending"}


async def list_invitations(db, plugin_id, installation_id, user_id, payload):
    if payload:
        raise ValueError("Invitation listing accepts no parameters")
    await _owned_flow(db, plugin_id, installation_id, user_id)
    rows = list(
        await db.scalars(
            select(UserInvitation)
            .where(
                UserInvitation.plugin_id == plugin_id,
                UserInvitation.installation_id == installation_id,
            )
            .order_by(UserInvitation.created_at.desc(), UserInvitation.id)
            .limit(100)
        )
    )
    result = []
    for row in rows:
        delivery = await db.scalar(
            select(NotificationDelivery.status)
            .where(
                NotificationDelivery.notification_id == row.notification_id,
            )
            .limit(1)
        )
        state = (
            "accepted"
            if row.used_at
            else "revoked"
            if row.revoked_at
            else ("expired" if row.expires_at <= int(time.time()) else "pending")
        )
        result.append(
            {
                "id": str(row.id),
                "email": decrypt_secret(row.encrypted_email),
                "status": state,
                "delivery_status": delivery,
                "created_at": row.created_at,
                "expires_at": row.expires_at,
            }
        )
    return {"invitations": result}


async def revoke_invitation(db, plugin_id, installation_id, user_id, payload):
    identity = UserInviteIdentity.model_validate(payload)
    await db.execute(select(func.pg_advisory_xact_lock(731856417203)))
    await _owned_flow(db, plugin_id, installation_id, user_id)
    row = await db.scalar(
        select(UserInvitation)
        .where(
            UserInvitation.id == identity.id,
            UserInvitation.plugin_id == plugin_id,
            UserInvitation.installation_id == installation_id,
        )
        .with_for_update()
    )
    if row is None:
        raise LookupError("Invitation not found for this installation")
    await _retire(db, row, revoked=True)
    await db.commit()
    return {"id": str(row.id), "revoked": True}


async def _retire(db, row, *, revoked=False):
    now = int(time.time())
    row.encrypted_token = None
    if revoked and row.used_at is None:
        row.revoked_at = now
    await db.execute(
        update(NotificationPendingRecipient)
        .where(
            NotificationPendingRecipient.id == row.pending_recipient_id,
        )
        .values(retired_at=now)
    )
    await db.execute(
        update(NotificationDestination)
        .where(
            NotificationDestination.pending_recipient_id == row.pending_recipient_id,
        )
        .values(active=False, enabled=False, encrypted_configuration=None)
    )
    await change_deliveries(
        db,
        update(NotificationDelivery)
        .where(
            NotificationDelivery.notification_id == row.notification_id,
            NotificationDelivery.status.in_(ACTIVE_DELIVERY_STATUSES),
        )
        .values(status="cancelled", claim_token=None, lease_until=None),
    )


async def accept_invitation(db, token_id, token, username, password):
    validate_password(password)
    username = validate_username(username)
    # Same lock order as revoke/replacement prevents stale acceptance across withdrawal.
    await db.execute(select(func.pg_advisory_xact_lock(731856417203)))
    row = await db.scalar(
        select(UserInvitation)
        .where(UserInvitation.id == token_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if not await invitation_is_active(db, row) or not matches_invitation_token(row, token):
        return False
    email = decrypt_secret(row.encrypted_email)
    conflict = await db.scalar(
        select(User.id).where(
            or_(
                func.lower(User.email) == email,
                func.lower(User.username) == email,
                User.username == username,
                func.lower(User.email) == username.lower(),
            )
        )
    )
    if conflict is not None:
        raise ValueError(
            "The email or username is already in use. Choose another username or contact the administrator"
        )
    user = User(
        username=username,
        email=email,
        password_hash=await asyncio.to_thread(hash_password, password),
        is_active=True,
        is_admin=False,
    )
    db.add(user)
    row.used_at = int(time.time())
    await _retire(db, row)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise ValueError("The email or username is already in use") from exc
    return True


async def retire_invitations(db, plugin_id, installation_id=None):
    query = select(UserInvitation).where(
        UserInvitation.plugin_id == plugin_id,
        UserInvitation.used_at.is_(None),
        UserInvitation.revoked_at.is_(None),
        UserInvitation.encrypted_token.is_not(None),
    )
    if installation_id is not None:
        query = query.where(UserInvitation.installation_id == installation_id)
    for row in await db.scalars(query.with_for_update()):
        await _retire(db, row, revoked=True)


async def expire_invitations(db):
    now = int(time.time())
    rows = list(
        await db.scalars(
            select(UserInvitation)
            .where(
                UserInvitation.expires_at <= now,
                UserInvitation.encrypted_token.is_not(None),
            )
            .limit(100)
            .with_for_update(skip_locked=True)
        )
    )
    for row in rows:
        await _retire(db, row)
    expired = (
        select(NotificationPendingRecipient.id)
        .where(
            NotificationPendingRecipient.expires_at < now - 86400,
        )
        .limit(100)
    )
    # Delete delivery FKs before the recipient cascades its destinations/notice.
    endpoints = select(NotificationDestination.id).where(
        NotificationDestination.pending_recipient_id.in_(expired)
    )
    await db.execute(
        delete(NotificationDelivery).where(NotificationDelivery.destination_id.in_(endpoints))
    )
    await db.execute(
        delete(NotificationPendingRecipient).where(NotificationPendingRecipient.id.in_(expired))
    )
    await db.commit()
