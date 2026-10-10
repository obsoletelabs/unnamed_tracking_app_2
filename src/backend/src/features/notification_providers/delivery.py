"""Core-owned notification delivery creation, retry, and terminal state."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.preferences import load_preferences
from src.database.models.notification import Notification
from src.database.models.notification_delivery import NotificationDelivery
from src.database.models.notification_delivery_attempt import NotificationDeliveryAttempt
from src.database.models.notification_destination import NotificationDestination
from src.database.models.notification_provider_setting import NotificationProviderSetting
from src.database.models.user import User
from src.features.notification_audit import change_deliveries, record_deliveries
from src.features.notification_destinations import resolve_destinations
from src.features.notification_policy import INBOX_PROVIDER, Trust, route_choice, select_projection
from src.features.notification_source_policy import source_delivery_allowed
from src.plugin_api.runtime_client import PluginRuntimeUnavailable

from .base import DeliveryResult, NotificationProvider, ProviderDestination, notification_message
from .registry import get_notification_providers

logger = logging.getLogger(__name__)
MAX_ATTEMPTS = 3
LEASE_SECONDS = 90
TRANSPORT_TIMEOUT_SECONDS = 35


async def ensure_deliveries(
    db: AsyncSession, notification_ids: list[UUID], *, preferences: dict[str, Any] | None = None
) -> None:
    """Resolve concrete endpoints and persist only eligible work, without runtime I/O."""
    if not notification_ids:
        return
    now = int(time.time())
    notifications = await db.scalars(
        select(Notification).where(Notification.id.in_(notification_ids))
    )
    for notification in notifications:
        prefs = preferences or await load_preferences(db, notification.user_id)
        enabled_providers = set(
            await db.scalars(
                select(NotificationProviderSetting.provider_id).where(
                    NotificationProviderSetting.user_id == notification.user_id,
                    NotificationProviderSetting.enabled.is_(True),
                )
            )
        )
        for destination in await resolve_destinations(db, notification.user_id):
            if (
                destination.provider_id != INBOX_PROVIDER
                and destination.provider_id not in enabled_providers
            ):
                continue
            projection = select_projection(notification, destination, prefs)
            if projection is None:
                continue
            inbox = destination.provider_id == INBOX_PROVIDER
            delivery_id = await db.scalar(
                pg_insert(NotificationDelivery)
                .values(
                    notification_id=notification.id,
                    provider_id=destination.provider_id,
                    destination_id=destination.id,
                    destination_revision=destination.revision,
                    projection=projection,
                    requested_urgency=route_choice(
                        notification.event_type, str(destination.id), prefs
                    )["urgency"],
                    status="sent" if inbox else "pending",
                    attempts=0,
                    next_attempt_at=now,
                )
                .on_conflict_do_nothing(index_elements=["notification_id", "destination_id"])
                .returning(NotificationDelivery.id)
            )
            await record_deliveries(db, [delivery_id] if delivery_id else [])
            if inbox:
                notification.inbox_visible = True
    await db.flush()


async def _claim(db: AsyncSession) -> tuple[UUID, UUID] | None:
    """Commit a lease before any network work; competing workers skip locked rows."""
    now = int(time.time())
    delivery = await db.scalar(
        select(NotificationDelivery)
        .where(
            or_(
                (NotificationDelivery.status.in_(("pending", "retry_wait")))
                & (NotificationDelivery.next_attempt_at <= now),
                (NotificationDelivery.status == "processing")
                & (NotificationDelivery.lease_until <= now),
            )
        )
        .order_by(NotificationDelivery.next_attempt_at, NotificationDelivery.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if delivery is None:
        await db.commit()
        return None
    if delivery.status == "processing":
        await db.execute(
            update(NotificationDeliveryAttempt)
            .where(
                NotificationDeliveryAttempt.delivery_id == delivery.id,
                NotificationDeliveryAttempt.finished_at.is_(None),
            )
            .values(finished_at=now, outcome="unknown", error_code="lease_expired")
        )
    token = uuid4()
    delivery.status = "processing"
    delivery.claim_token = token
    delivery.lease_until = now + LEASE_SECONDS
    identity = (delivery.id, token)
    await db.flush()
    await record_deliveries(db, [delivery.id])
    await db.commit()
    return identity


async def _finish(
    db: AsyncSession, delivery_id: UUID, token: UUID, status: str, error: str | None = None
) -> None:
    await change_deliveries(
        db,
        update(NotificationDelivery)
        .where(
            NotificationDelivery.id == delivery_id,
            NotificationDelivery.claim_token == token,
            NotificationDelivery.status == "processing",
        )
        .values(
            status=status,
            last_error=error,
            claim_token=None,
            lease_until=None,
            next_attempt_at=int(time.time()) + 60,
        ),
    )
    await db.commit()


@dataclass(frozen=True)
class _DeliveryWork:
    notification: Notification
    endpoint: NotificationDestination
    provider: NotificationProvider
    destination: ProviderDestination


async def _lookup_destination(provider, db, user, setting, endpoint):
    lookup = getattr(provider, "lookup_endpoint", None)
    if lookup is not None:
        destination = await lookup(db, user, setting, endpoint)
        if destination is not None and (
            destination.user_id != endpoint.user_id
            or destination.endpoint_id != endpoint.id
            or destination.endpoint_revision != endpoint.revision
        ):
            return None
        return destination
    return await provider.lookup_destination(db, user, setting)


def _transport_allowed(notification: Notification, destination: ProviderDestination) -> bool:
    return destination.allows_sensitive or (
        notification.required_trust < Trust.SECURE
        and notification.purpose not in {"security", "recovery", "verification"}
    )


async def _refresh_destination(db: AsyncSession, work: _DeliveryWork) -> ProviderDestination | None:
    if getattr(work.provider, "lookup_endpoint", None) is None:
        return work.destination
    user = await db.get(User, work.notification.user_id, populate_existing=True)
    if user is None or not user.is_active:
        return None
    setting = await db.scalar(
        select(NotificationProviderSetting)
        .where(
            NotificationProviderSetting.user_id == user.id,
            NotificationProviderSetting.provider_id == work.provider.id,
        )
        .execution_options(populate_existing=True)
    )
    return await _lookup_destination(work.provider, db, user, setting, work.endpoint)


# Each denial records a distinct lifecycle reason; explicit exits keep the
# security gates reviewable without nesting network and database operations.
# pylint: disable-next=too-many-return-statements
async def _resolve_work(
    db: AsyncSession, delivery: NotificationDelivery, token: UUID
) -> _DeliveryWork | None:
    delivery_id = delivery.id
    notification = await db.get(Notification, delivery.notification_id, populate_existing=True)
    endpoint = await db.get(NotificationDestination, delivery.destination_id)
    now = int(time.time())
    if notification is None or notification.deleted_at is not None:
        await _finish(db, delivery_id, token, "cancelled")
        return None
    if not await source_delivery_allowed(db, notification):
        await _finish(db, delivery_id, token, "suppressed", "source_revoked")
        return None
    prefs = await load_preferences(db, notification.user_id)
    projection = select_projection(notification, endpoint, prefs) if endpoint else None
    if (
        endpoint is None
        or endpoint.revision != delivery.destination_revision
        or projection != delivery.projection
        or (notification.expires_at is not None and notification.expires_at <= now)
    ):
        await _finish(db, delivery_id, token, "suppressed", "routing_changed")
        return None
    providers = await get_notification_providers(db)
    provider = providers.get(delivery.provider_id)
    if provider is None:
        await _finish(db, delivery_id, token, "suppressed", "provider_revoked")
        return None
    registration = getattr(provider, "registration", None)
    if registration is not None and registration.installation_id != endpoint.installation_id:
        await _finish(db, delivery_id, token, "suppressed", "installation_changed")
        return None
    user = await db.get(User, notification.user_id)
    if user is None or not user.is_active:
        await _finish(db, delivery_id, token, "suppressed", "user_inactive")
        return None
    setting = await db.scalar(
        select(NotificationProviderSetting).where(
            NotificationProviderSetting.user_id == user.id,
            NotificationProviderSetting.provider_id == delivery.provider_id,
        )
    )
    try:
        destination = await asyncio.wait_for(
            _lookup_destination(provider, db, user, setting, endpoint), TRANSPORT_TIMEOUT_SECONDS
        )
    except (PluginRuntimeUnavailable, TimeoutError):
        await _finish(db, delivery_id, token, "retry_wait", "provider_unavailable")
        return None
    if destination is None:
        await _finish(db, delivery_id, token, "suppressed", "authorization_unavailable")
        return None
    if not _transport_allowed(notification, destination):
        await _finish(db, delivery_id, token, "suppressed", "transport_security_required")
        return None
    if delivery.attempts >= MAX_ATTEMPTS:
        await _finish(db, delivery_id, token, "failed_permanent", "attempts_exhausted")
        return None
    return _DeliveryWork(notification, endpoint, provider, destination)


# Separate exits ensure source revocation is rechecked before transport I/O.
# pylint: disable-next=too-many-return-statements
async def _dispatch(db: AsyncSession, delivery_id: UUID, token: UUID) -> bool:
    delivery = await db.get(NotificationDelivery, delivery_id, populate_existing=True)
    if delivery is None or delivery.status != "processing" or delivery.claim_token != token:
        return False
    work = await _resolve_work(db, delivery, token)
    if work is None:
        return False
    notification, endpoint = work.notification, work.endpoint
    # Claim revalidation prevents a delete/revocation that happened during lookup
    # from releasing new transport work. Deletion after this commit is best effort.
    await db.refresh(delivery)
    await db.refresh(notification)
    if (
        delivery.status != "processing"
        or delivery.claim_token != token
        or notification.deleted_at is not None
    ):
        await db.commit()
        return False
    await db.refresh(endpoint)
    if not await source_delivery_allowed(db, notification):
        await _finish(db, delivery_id, token, "suppressed", "source_revoked")
        return False
    current_preferences = await load_preferences(db, notification.user_id)
    if (
        endpoint.revision != delivery.destination_revision
        or select_projection(notification, endpoint, current_preferences) != delivery.projection
    ):
        await _finish(db, delivery_id, token, "suppressed", "routing_changed")
        return False
    # Built-in endpoint transports resolve again after the final policy refresh;
    # a deployment TLS change cannot reuse the earlier configuration snapshot.
    destination = await _refresh_destination(db, work)
    if destination is None or not _transport_allowed(notification, destination):
        await _finish(db, delivery_id, token, "suppressed", "transport_security_required")
        return False
    delivery.requested_urgency = route_choice(
        notification.event_type, str(endpoint.id), current_preferences
    )["urgency"]
    delivery.effective_urgency = (
        delivery.requested_urgency
        if getattr(work.provider, "critical_supported", False) is True
        else "normal"
    )
    delivery.attempts += 1
    now = int(time.time())
    delivery.attempted_at = now
    attempt = NotificationDeliveryAttempt(
        delivery_id=delivery.id, number=delivery.attempts, claim_token=token, started_at=now
    )
    db.add(attempt)
    await db.flush()
    message = notification_message(
        notification,
        projection=delivery.projection,
        attempt_id=attempt.id,
        urgency=delivery.effective_urgency,
    )
    await db.commit()
    try:
        result = await asyncio.wait_for(
            work.provider.deliver(db, destination, message), TRANSPORT_TIMEOUT_SECONDS
        )
    # Providers are third-party failure boundaries. Never expose endpoint secrets.
    except Exception:  # pylint: disable=broad-exception-caught
        result = DeliveryResult(success=False, retryable=True, error="provider_error")
    attempt.finished_at = int(time.time())
    attempt.outcome = "sent" if result.success else "failed"
    attempt.error_code = None if result.success else "provider_error"
    state = (
        "sent"
        if result.success
        else (
            "retry_wait"
            if result.retryable and delivery.attempts < MAX_ATTEMPTS
            else "failed_permanent"
        )
    )
    await db.flush()
    await change_deliveries(
        db,
        update(NotificationDelivery)
        .where(
            NotificationDelivery.id == delivery_id,
            NotificationDelivery.claim_token == token,
            NotificationDelivery.status == "processing",
        )
        .values(
            status=state,
            last_error=attempt.error_code,
            claim_token=None,
            lease_until=None,
            next_attempt_at=int(time.time()) + 60 * (2 ** (delivery.attempts - 1)),
        ),
    )
    await db.commit()
    return result.success


async def process_pending_deliveries(db: AsyncSession, limit: int = 5) -> int:
    """Bound work/time, isolate failures and retain ownership across worker crashes."""
    delivered = 0
    deadline = time.monotonic() + TRANSPORT_TIMEOUT_SECONDS
    for _ in range(limit):
        if time.monotonic() >= deadline:
            break
        claim = await _claim(db)
        if claim is None:
            break
        try:
            delivered += int(await _dispatch(db, *claim))
        # One adapter failure must not stop deliveries to other eligible endpoints.
        except Exception:  # pylint: disable=broad-exception-caught
            await db.rollback()
            logger.warning(
                "Notification dispatch failed: delivery_id=%s reason=dispatch_error", claim[0]
            )
            await _finish(db, *claim, "retry_wait", "dispatch_error")
    return delivered
