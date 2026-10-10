"""Explicit, session-bound browser enrollment; never automatic credential reassignment."""

import hashlib
import hmac
import json
from uuid import UUID

from fastapi import Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import settings
from src.core.crypto import encrypt_secret
from src.database.models.notification_destination import NotificationDestination
from src.database.models.notification_provider_setting import NotificationProviderSetting
from src.database.models.user import User
from src.features.notification_browser import (
    BrowserSubscription,
    bound_subscription,
    browser_context,
    browser_session,
    retire_browser,
)
from src.features.notification_enrollment import (
    EnrollmentError,
    enforce_destination_limit,
    invalidate_endpoint,
    owned_destination,
)
from src.features.notification_push_config import PUSH_PROVIDER


async def enroll_browser(
    db: AsyncSession,
    request: Request,
    user_id: UUID,
    subscription: BrowserSubscription,
    *,
    public_key: str,
    label: str,
) -> UUID:
    session = await browser_session(db, request, user_id)
    if session is None:
        raise EnrollmentError("Sign in with a browser session to enroll this device", 403)
    context = await browser_context(db)
    if context is None or public_key != context.configuration.public_key:
        raise EnrollmentError("Browser push is unavailable or its server key changed", 409)
    if subscription.expired:
        raise EnrollmentError("This browser subscription has expired")
    key = hmac.new(
        settings.SECRET_KEY.encode(), subscription.endpoint.encode(), hashlib.sha256
    ).hexdigest()
    # Serialize this concrete endpoint across owners, then take the usual owner lock.
    lock_key = int(key[:16], 16) - 2**63
    await db.execute(select(func.pg_advisory_xact_lock(lock_key)))
    await db.scalar(select(User.id).where(User.id == user_id).with_for_update())
    previous = list(
        await db.scalars(
            select(NotificationDestination)
            .where(
                NotificationDestination.provider_id == PUSH_PROVIDER,
                NotificationDestination.endpoint_key == key,
            )
            .with_for_update()
        )
    )
    for endpoint in previous:
        if (
            endpoint.user_id == user_id
            and endpoint.configuration_ref == str(session.id)
            and endpoint.enabled
            and await bound_subscription(db, endpoint, context) == subscription
        ):
            endpoint.display_name = label.strip() or "This browser"
            await db.commit()
            return endpoint.id
        await retire_browser(db, endpoint, "browser_reenrolled")
    await db.flush()
    await enforce_destination_limit(db, user_id, PUSH_PROVIDER)
    endpoint = NotificationDestination(
        user_id=user_id,
        provider_id=PUSH_PROVIDER,
        endpoint_key=key,
        kind="browser_push",
        channel_context="external",
        privacy=1,
        installation_id=context.installation_id,
        configuration_ref=str(session.id),
        display_name=label.strip() or "This browser",
        encrypted_configuration=encrypt_secret(
            json.dumps(
                {
                    "public_key": public_key,
                    "subscription": subscription.model_dump(by_alias=True),
                }
            )
        ),
    )
    db.add(endpoint)
    setting = await db.scalar(
        select(NotificationProviderSetting).where(
            NotificationProviderSetting.user_id == user_id,
            NotificationProviderSetting.provider_id == PUSH_PROVIDER,
        )
    )
    if setting is None:
        db.add(
            NotificationProviderSetting(user_id=user_id, provider_id=PUSH_PROVIDER, enabled=True)
        )
    await db.commit()
    return endpoint.id


async def update_browser(
    db: AsyncSession, user_id: UUID, destination_id: UUID, changes: dict
) -> None:
    endpoint = await owned_destination(db, user_id, destination_id, provider_id=PUSH_PROVIDER)
    if changes.get("remove"):
        await retire_browser(db, endpoint, "browser_removed")
    else:
        if "label" in changes:
            endpoint.display_name = changes["label"].strip() or "Browser"
        if "enabled" in changes:
            endpoint.enabled = changes["enabled"]
            if endpoint.enabled:
                context = await browser_context(db)
                if context is None or await bound_subscription(db, endpoint, context) is None:
                    raise EnrollmentError(
                        "Enroll this browser again; its session or subscription changed", 409
                    )
            # Always invalidate unsent work; explicit enabling must not replay it.
            await invalidate_endpoint(db, endpoint)
    await db.commit()
