"""Core-owned notification delivery creation, retry, and terminal state."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, replace
from typing import Any
from uuid import UUID, uuid4, uuid5

from sqlalchemy import or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.preferences import load_preferences
from src.core.metrics import (
    NOTIFICATION_ATTEMPTS,
    NOTIFICATION_DELIVERIES,
    NOTIFICATION_DELIVERY_DURATION,
)
from src.database.models.notification import Notification
from src.database.models.notification_delivery import NotificationDelivery
from src.database.models.notification_delivery_attempt import NotificationDeliveryAttempt
from src.database.models.notification_destination import NotificationDestination
from src.database.models.notification_provider_setting import NotificationProviderSetting
from src.database.models.user import User
from src.features.notification_aggregation import (
    assign_window,
    count_member,
    item_link_base,
    maintain,
    plan_batch,
    settle_batch,
    settle_finished_lead,
)
from src.features.notification_audit import change_deliveries, record_deliveries
from src.features.notification_auth_secrets import recovery_message
from src.features.notification_destinations import resolve_destinations
from src.features.notification_pending import invitation_message
from src.features.notification_plugin_policy import delivery_projection
from src.features.notification_policy import INBOX_PROVIDER, Trust, route_choice
from src.features.notification_reconcile import (
    ReconcilePlan,
    has_prior_presentation,
    in_flight,
    mark_edit_failed,
    plan_operation,
    record_presented,
    supersede_unsent,
)
from src.features.notification_render import Rendering, render_message
from src.features.notification_source_policy import source_delivery_allowed
from src.features.notification_timing import decide_for
from src.plugin_api.runtime_client import PluginRuntimeUnavailable

from .base import (
    DeliveryResult,
    NotificationMessage,
    NotificationProvider,
    ProviderDestination,
    notification_message,
)
from .capabilities import ResolvedProfile, resolve_profile
from .registry import get_notification_providers

logger = logging.getLogger(__name__)
MAX_ATTEMPTS = 3
LEASE_SECONDS = 90
TRANSPORT_TIMEOUT_SECONDS = 35
_REASONS = frozenset({
    "source_revoked", "routing_changed", "provider_revoked", "installation_changed",
    "user_inactive", "provider_unavailable", "authorization_unavailable",
    "transport_security_required", "attempts_exhausted", "muted", "superseded",
    "revision_in_flight", "recovery_expired", "dispatch_error", "provider_error",
    "edit_failed", "delivered", "cancelled",
})


def _channel(provider_id: str) -> str:
    return {
        "core.browser": "browser", "core.smtp": "email", INBOX_PROVIDER: "inbox"
    }.get(provider_id, "plugin")


def _delivery_metric(provider_id: str, status: str, reason: str | None) -> None:
    outcome = status if status in {"sent", "retry_wait", "suppressed", "cancelled", "failed_permanent"} else "other"
    safe_reason = reason if reason in _REASONS else "other"
    NOTIFICATION_DELIVERIES.labels(_channel(provider_id), outcome, safe_reason).inc()


def retry_delay(attempts: int) -> int:
    """Seconds before the next try after ``attempts`` failed sends (exponential, from one minute)."""
    return 60 * (2 ** (attempts - 1))


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
        if notification.pending_recipient_id is not None:
            await _ensure_pending_deliveries(db, notification)
            continue
        if notification.user_id is None:
            continue
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
            await _queue_destination(db, notification, destination, prefs, now)
    await db.flush()


async def _queue_destination(
    db: AsyncSession,
    notification: Notification,
    destination: NotificationDestination,
    prefs: dict[str, Any],
    now: int,
) -> None:
    """Persist the delivery row (and window membership) one destination owes a notification."""
    projection = await delivery_projection(db, notification, destination, prefs)
    if projection is None:
        return
    inbox = destination.provider_id == INBOX_PROVIDER
    requested_urgency = route_choice(notification.event_type, str(destination.id), prefs)["urgency"]
    decision = decide_for(notification, destination, prefs, now=now)
    if decision.action == "mute":
        # A muted route leaves no queued work; the inbox keeps its history.
        return
    # The inbox shows one evolving item; every external destination owes each revision.
    revision = 0 if inbox or notification.story_id is None else notification.story_revision
    revision = revision or 0
    prior = False
    if revision:
        await supersede_unsent(db, notification.id, destination.id, revision)
        prior = await has_prior_presentation(db, notification.id, destination.id, revision)
    # The in-app inbox is never delayed; external destinations follow the timing policy.
    # An edit or follow-up for a story the destination already saw is not a new item to
    # hold in a digest window.
    window_id = (
        None
        if inbox or prior
        else await assign_window(db, notification, destination, decision=decision, now=now)
    )
    delivery_id = await db.scalar(
        pg_insert(NotificationDelivery)
        .values(
            notification_id=notification.id,
            provider_id=destination.provider_id,
            destination_id=destination.id,
            destination_revision=destination.revision,
            projection=projection,
            story_revision=revision,
            requested_urgency=requested_urgency,
            window_id=window_id,
            timing_reason=None if inbox or not window_id else decision.code[:64],
            status="sent" if inbox else "aggregating" if window_id else "pending",
            attempts=0,
            next_attempt_at=now,
        )
        .on_conflict_do_nothing(
            index_elements=["notification_id", "destination_id", "story_revision"]
        )
        .returning(NotificationDelivery.id)
    )
    if delivery_id and window_id:
        await count_member(db, window_id)
    await record_deliveries(db, [delivery_id] if delivery_id else [])
    if inbox:
        notification.inbox_visible = True


async def _ensure_pending_deliveries(db, notice):
    endpoints = await db.scalars(
        select(NotificationDestination).where(
            NotificationDestination.pending_recipient_id == notice.pending_recipient_id,
            NotificationDestination.user_id.is_(None),
        )
    )
    for endpoint in endpoints:
        projection = await delivery_projection(db, notice, endpoint, {})
        if projection is not None:
            await db.execute(
                pg_insert(NotificationDelivery)
                .values(
                    notification_id=notice.id,
                    provider_id=endpoint.provider_id,
                    destination_id=endpoint.id,
                    destination_revision=endpoint.revision,
                    projection=projection,
                    status="pending",
                    next_attempt_at=int(time.time()),
                )
                .on_conflict_do_nothing(
                    index_elements=["notification_id", "destination_id", "story_revision"]
                )
            )


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
    delivery = await db.get(NotificationDelivery, delivery_id)
    changed = await change_deliveries(
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
    if status in {"suppressed", "cancelled", "failed_permanent"}:
        await settle_finished_lead(db, delivery_id, status, error)
    await db.commit()
    if changed and delivery is not None:
        _delivery_metric(delivery.provider_id, status, error or ("delivered" if status == "sent" else "cancelled"))


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
            or destination.pending_recipient_id != endpoint.pending_recipient_id
            or destination.endpoint_id != endpoint.id
            or destination.endpoint_revision != endpoint.revision
        ):
            return None
        return destination
    return await provider.lookup_destination(db, user, setting)


def _transport_allowed(notification: Notification, destination: ProviderDestination) -> bool:
    return destination.allows_sensitive or (
        notification.required_trust < Trust.SECURE
        and notification.purpose not in {"security", "recovery", "verification", "invitation"}
    )


async def _refresh_destination(db: AsyncSession, work: _DeliveryWork) -> ProviderDestination | None:
    if getattr(work.provider, "lookup_endpoint", None) is None:
        return work.destination
    if work.notification.pending_recipient_id is not None:
        return await _lookup_destination(work.provider, db, None, None, work.endpoint)
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
    prefs = await load_preferences(db, notification.user_id) if notification.user_id else {}
    projection = await delivery_projection(db, notification, endpoint, prefs) if endpoint else None
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
    user = await db.get(User, notification.user_id) if notification.user_id else None
    if notification.pending_recipient_id is None and (user is None or not user.is_active):
        await _finish(db, delivery_id, token, "suppressed", "user_inactive")
        return None
    setting = await db.scalar(
        select(NotificationProviderSetting).where(
            NotificationProviderSetting.user_id == notification.user_id,
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


async def _revalidate(
    db: AsyncSession, delivery: NotificationDelivery, work: _DeliveryWork, token: UUID
) -> ProviderDestination | None:
    """Recheck lease, policy, grants and destination; finishes the delivery when any gate fails.

    Run once after claiming and again immediately before every physical send, edit or follow-up,
    so a revocation or a deletion that happens in between never releases transport work.
    """
    delivery_id, notification, endpoint = delivery.id, work.notification, work.endpoint
    await db.refresh(delivery)
    await db.refresh(notification)
    if (
        delivery.status != "processing"
        or delivery.claim_token != token
        or notification.deleted_at is not None
    ):
        await db.commit()
        return None
    await db.refresh(endpoint)
    if not await source_delivery_allowed(db, notification):
        await _finish(db, delivery_id, token, "suppressed", "source_revoked")
        return None
    preferences = await load_preferences(db, notification.user_id) if notification.user_id else {}
    if (
        endpoint.revision != delivery.destination_revision
        or await delivery_projection(db, notification, endpoint, preferences) != delivery.projection
    ):
        await _finish(db, delivery_id, token, "suppressed", "routing_changed")
        return None
    if (
        endpoint.provider_id != INBOX_PROVIDER
        and decide_for(notification, endpoint, preferences, now=int(time.time())).action == "mute"
    ):
        await _finish(db, delivery_id, token, "suppressed", "muted")
        return None
    # Built-in endpoint transports resolve again after the final policy refresh;
    # a deployment TLS change cannot reuse the earlier configuration snapshot.
    destination = await _refresh_destination(db, work)
    if destination is None or not _transport_allowed(notification, destination):
        await _finish(db, delivery_id, token, "suppressed", "transport_security_required")
        return None
    return destination


@dataclass(frozen=True)
class _Prepared:
    plan: ReconcilePlan
    rendering: Rendering
    profile: ResolvedProfile
    link_base: str | None


def _fallback_codes(*groups: tuple[str, ...]) -> str | None:
    codes = dict.fromkeys(code for group in groups for code in group)
    return ",".join(codes)[:160] or None


async def _finish_unsent(
    db: AsyncSession, delivery: NotificationDelivery, token: UUID, prepared_plan: ReconcilePlan
) -> bool:
    """Close a delivery that needs no provider call (policy, or already presented)."""
    status, reason = prepared_plan.outcome or ("sent", "")
    delivery.operation = prepared_plan.operation
    delivery.fallback = _fallback_codes(prepared_plan.fallbacks)
    await _finish(db, delivery.id, token, status, reason or None)
    return status == "sent"


async def _prepare(
    db: AsyncSession,
    delivery: NotificationDelivery,
    work: _DeliveryWork,
    token: UUID,
    base: NotificationMessage,
) -> _Prepared | None:
    """Plan the operation and render for the live profile; None when nothing is left to send."""
    notification, endpoint = work.notification, work.endpoint
    profile = resolve_profile(work.provider)
    now = int(time.time())
    if delivery.story_revision and notification.story_revision is not None:
        if notification.story_revision > delivery.story_revision and delivery.batch_id is None:
            await _finish(db, delivery.id, token, "suppressed", "superseded")
            return None
    plan = await plan_operation(db, delivery, notification, profile)
    if delivery.story_revision and await in_flight(db, delivery):
        # The earlier revision's remote reference is not committed yet; do not race it.
        delivery.next_attempt_at = now + 5
        await _finish(db, delivery.id, token, "retry_wait", "revision_in_flight")
        return None
    if plan.outcome is not None:
        await _finish_unsent(db, delivery, token, plan)
        return None
    link_base = await item_link_base(db, profile, endpoint.id)
    rendering = await render_message(
        db,
        base,
        notification,
        projection=delivery.projection,
        profile=profile,
        operation=plan.operation,
        remote_ref=plan.reference.remote_ref if plan.reference else None,
        link_base=link_base,
        revision=delivery.story_revision,
        idempotency_key=uuid5(delivery.id, f"{plan.operation}:{delivery.story_revision}"),
    )
    reference = plan.reference
    if (
        plan.operation in {"edit", "follow_up"}
        and reference is not None
        and reference.content_hash == rendering.content_hash
    ):
        # A new revision whose presentation is identical changes nothing for the reader.
        await record_presented(
            db,
            (delivery.id,),
            remote_ref=reference.remote_ref,
            content_hash=rendering.content_hash,
            keep_reference=True,
        )
        unchanged = replace(
            plan, outcome=("sent", "unchanged"), fallbacks=(*plan.fallbacks, "unchanged")
        )
        await _finish_unsent(db, delivery, token, unchanged)
        return None
    return _Prepared(plan, rendering, profile, link_base)


async def _provider_call(
    db: AsyncSession, work: _DeliveryWork, destination: ProviderDestination, message, operation: str
) -> DeliveryResult:
    channel = _channel(work.endpoint.provider_id)
    started = time.perf_counter()
    outcome = "failed"
    try:
        if operation == "edit":
            edit = work.provider.edit  # type: ignore[attr-defined]
            result = await asyncio.wait_for(edit(db, destination, message), TRANSPORT_TIMEOUT_SECONDS)
        else:
            result = await asyncio.wait_for(
                work.provider.deliver(db, destination, message), TRANSPORT_TIMEOUT_SECONDS
            )
        outcome = "sent" if result.success else "failed"
        return result
    except asyncio.CancelledError:
        outcome = "cancelled"
        raise
    finally:
        NOTIFICATION_ATTEMPTS.labels(channel, outcome).inc()
        NOTIFICATION_DELIVERY_DURATION.labels(channel, outcome).observe(time.perf_counter() - started)


async def _close_attempt(db: AsyncSession, attempt_id: UUID, outcome: str, error: str | None):
    await db.execute(
        update(NotificationDeliveryAttempt)
        .where(NotificationDeliveryAttempt.id == attempt_id)
        .values(finished_at=int(time.time()), outcome=outcome, error_code=error)
    )
    await db.commit()


# Separate exits ensure source revocation is rechecked before transport I/O.
# pylint: disable-next=too-many-return-statements,too-many-locals,too-many-statements
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
    if await _revalidate(db, delivery, work, token) is None:
        return False
    current_preferences = (
        await load_preferences(db, notification.user_id) if notification.user_id else {}
    )
    delivery.requested_urgency = route_choice(
        notification.event_type, str(endpoint.id), current_preferences
    )["urgency"]
    delivery.effective_urgency = (
        delivery.requested_urgency
        if getattr(work.provider, "critical_supported", False) is True
        else "normal"
    )
    base = notification_message(
        notification, projection=delivery.projection, urgency=delivery.effective_urgency
    )
    prepared = await _prepare(db, delivery, work, token, base)
    if prepared is None:
        await db.refresh(delivery)
        return delivery.status == "sent"
    plan, message = prepared.plan, prepared.rendering.message
    delivery.attempts += 1
    now = int(time.time())
    delivery.attempted_at = now
    delivery.operation = plan.operation
    attempt = NotificationDeliveryAttempt(
        delivery_id=delivery.id,
        number=delivery.attempts,
        claim_token=token,
        started_at=now,
        operation=plan.operation,
        batch_id=delivery.batch_id,
    )
    db.add(attempt)
    await db.flush()
    attempt_id = attempt.id
    message = replace(message, attempt_id=attempt_id)
    included: tuple[UUID, ...] = (delivery.id,)
    if delivery.batch_id is not None:
        batch = await plan_batch(
            db,
            delivery,
            notification,
            endpoint,
            current_preferences,
            base=message,
            transport=getattr(work.provider, "transport", None),
            profile=prepared.profile,
            link_base=prepared.link_base,
        )
        message, included = batch.message, batch.included
    fallback = _fallback_codes(plan.fallbacks, prepared.rendering.fallbacks)
    delivery.fallback = fallback
    await db.commit()
    try:
        message = await recovery_message(db, notification, endpoint, message)
        if message is not None:
            message = await invitation_message(db, notification, endpoint, message)
        if message is None:
            await _close_attempt(db, attempt_id, "skipped", "recovery_expired")
            await _finish(db, delivery_id, token, "suppressed", "recovery_expired")
            return False
        # Final gate: nothing may have been revoked, deleted or re-routed since the lease.
        destination = await _revalidate(db, delivery, work, token)
        if destination is None:
            await _close_attempt(db, attempt_id, "skipped", delivery.last_error)
            return False
        result = await _provider_call(db, work, destination, message, plan.operation)
    # Providers are third-party failure boundaries. Never expose endpoint secrets.
    except Exception:  # pylint: disable=broad-exception-caught
        result = DeliveryResult(success=False, retryable=True, error="provider_error")
    refused = (
        plan.operation == "edit" and not result.success and (result.gone or not result.retryable)
    )
    attempt = await db.merge(attempt)
    attempt.finished_at = int(time.time())
    attempt.outcome = "sent" if result.success else "failed"
    attempt.error_code = (
        None if result.success else "edit_failed" if plan.operation == "edit" else "provider_error"
    )
    state = (
        "sent"
        if result.success
        else (
            "retry_wait"
            if (result.retryable or refused) and delivery.attempts < MAX_ATTEMPTS
            else "failed_permanent"
        )
    )
    next_attempt = int(time.time()) + retry_delay(delivery.attempts)
    if refused:
        # The remote message cannot be edited: stop pretending, and follow up instead.
        await mark_edit_failed(db, plan.reference)
        delivery.operation = "follow_up"
        fallback = _fallback_codes(plan.fallbacks, prepared.rendering.fallbacks, ("edit_failed",))
        next_attempt = int(time.time())
    elif result.success:
        await record_presented(
            db,
            included,
            remote_ref=result.remote_ref,
            content_hash=prepared.rendering.content_hash,
            keep_reference=plan.operation == "edit",
        )
    await db.flush()
    changed = await change_deliveries(
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
            fallback=fallback,
            operation=delivery.operation,
            claim_token=None,
            lease_until=None,
            next_attempt_at=next_attempt,
        ),
    )
    await settle_batch(db, delivery_id, included, state, attempt.error_code)
    await db.commit()
    if changed:
        _delivery_metric(delivery.provider_id, state, attempt.error_code or "delivered")
    return result.success


async def _fail_dispatch(db: AsyncSession, delivery_id: UUID, token: UUID) -> None:
    """An unexpected error before the attempt was counted still uses one attempt up.

    Otherwise a delivery that fails deterministically (for example content that can no longer be
    rendered) would be reclaimed every minute forever instead of ending as `failed_permanent`.
    """
    await db.execute(
        update(NotificationDelivery)
        .where(
            NotificationDelivery.id == delivery_id,
            NotificationDelivery.claim_token == token,
            NotificationDelivery.status == "processing",
        )
        .values(attempts=NotificationDelivery.attempts + 1)
    )
    attempts = await db.scalar(
        select(NotificationDelivery.attempts).where(NotificationDelivery.id == delivery_id)
    )
    exhausted = attempts is not None and attempts >= MAX_ATTEMPTS
    await _finish(
        db, delivery_id, token, "failed_permanent" if exhausted else "retry_wait", "dispatch_error"
    )


async def process_pending_deliveries(db: AsyncSession, limit: int = 5) -> int:
    """Bound work/time, isolate failures and retain ownership across worker crashes."""
    delivered = 0
    try:
        await maintain(db)
    # Aggregation upkeep must never block deliveries that are already due.
    except Exception:  # pylint: disable=broad-exception-caught
        await db.rollback()
        logger.warning("Notification aggregation upkeep failed: reason=maintenance_error")
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
            await _fail_dispatch(db, *claim)
    return delivered
