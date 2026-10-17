"""Durable aggregation windows, batch planning and settlement for external deliveries.

A delivery that policy holds is persisted as ``aggregating`` and joined to a window row. Once a
window is due, one worker seals it under a row lock into deterministic chunks. The first member of
each chunk (the lead) becomes ordinary ``pending`` work and travels through the normal claim, lease,
retry and revalidation path; the other members stay ``aggregating`` and are settled together with
the lead in the same transaction. Nothing is held in memory, so restarts and overlapping workers
can neither lose nor double a batch beyond the existing at-least-once provider semantics.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, replace
from uuid import UUID, uuid5

from sqlalchemy import and_, delete, exists, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models.notification import Notification
from src.database.models.notification_aggregation import NotificationAggregationWindow
from src.database.models.notification_delivery import (
    ACTIVE_DELIVERY_STATUSES,
    NotificationDelivery,
)
from src.database.models.notification_destination import NotificationDestination
from src.database.models.plugin_notification_provider import PluginNotificationProviderRegistration
from src.features.notification_aggregation_policy import (
    MAX_WINDOW_MEMBERS,
    SEALED_WINDOW_RETENTION_SECONDS,
    AggregationPolicy,
    chunk_items,
    compose_digest,
    provider_limits,
    scope_key,
    window_bounds,
)
from src.features.notification_plugin_policy import delivery_projection
from src.features.notification_policy import INBOX_PROVIDER
from src.features.notification_providers.base import (
    DigestItem,
    NotificationMessage,
    notification_message,
)
from src.features.notification_providers.capabilities import ResolvedProfile, resolve_profile
from src.features.notification_source_policy import source_delivery_allowed
from src.features.notification_timing import decide_for
from src.features.notification_timing_policy import TimingDecision
from src.features.notification_urls import notification_url

LIVE_LEAD_STATUSES = ("pending", "processing", "retry_wait")
# A failed lead with one of these outcomes says the destination, not the member, is unusable.
DESTINATION_FAILURES = frozenset(
    {
        "provider_revoked",
        "installation_changed",
        "authorization_unavailable",
        "transport_security_required",
        "user_inactive",
        "attempts_exhausted",
    }
)
LABELS = {
    "media.episode.aired": "new episodes",
    "media.season.started": "seasons started",
    "media.sequel.announced": "sequels announced",
    "media.movie.released": "movie releases",
    "game.released": "game releases",
    "game.sale.started": "game sales",
    "system.plugin.update": "plugin updates",
}


async def assign_window(
    db: AsyncSession,
    notification: Notification,
    destination: NotificationDestination,
    *,
    decision: TimingDecision,
    now: int,
) -> UUID | None:
    """Return the window a new delivery must join, or None for immediate delivery.

    ``decision`` is the effective timing policy: either a held release (quiet hours or a user
    digest schedule, one shared window per release instant) or an aggregation policy.
    """
    if decision.action == "hold" and decision.release_at is not None:
        policy = AggregationPolicy("digest", max(decision.release_at - now, 1), "event_type")
        key = f"timing:{decision.release_at}"
        bounds = (now, decision.release_at)
    elif decision.action == "aggregate" and decision.aggregation.strategy != "immediate":
        policy = decision.aggregation
        key = scope_key(
            policy,
            event_type=notification.event_type,
            media_id=notification.media_id,
            event_at=notification.event_at,
            group_key=notification.group_key or "",
        )
        bounds = window_bounds(policy, now)
    else:
        return None
    for _ in range(3):
        window = await db.scalar(
            select(NotificationAggregationWindow)
            .where(
                NotificationAggregationWindow.destination_id == destination.id,
                NotificationAggregationWindow.destination_revision == destination.revision,
                NotificationAggregationWindow.strategy == policy.strategy,
                NotificationAggregationWindow.scope_key == key,
                NotificationAggregationWindow.status == "open",
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if window is not None and (
            window.window_end <= now or window.member_count >= MAX_WINDOW_MEMBERS
        ):
            # Bounded state: close it early; the sealer plans it and a new window opens.
            window.status = "full"
            window.window_end = min(window.window_end, now)
            await db.flush()
            window = None
        if window is not None:
            return window.id
        start, end = bounds
        await db.execute(
            pg_insert(NotificationAggregationWindow)
            .values(
                user_id=destination.user_id,
                destination_id=destination.id,
                destination_revision=destination.revision,
                provider_id=destination.provider_id,
                strategy=policy.strategy,
                scope_key=key,
                status="open",
                window_start=start,
                window_end=end,
                member_count=0,
                created_at=now,
            )
            .on_conflict_do_nothing()
        )
    raise RuntimeError("Aggregation window could not be opened")


async def count_member(db: AsyncSession, window_id: UUID) -> None:
    await db.execute(
        update(NotificationAggregationWindow)
        .where(NotificationAggregationWindow.id == window_id)
        .values(member_count=NotificationAggregationWindow.member_count + 1)
    )


async def live_profile(db: AsyncSession, provider_id: str) -> ResolvedProfile | None:
    """Capability profile of the live adapter, or None when it is not (or no longer) available."""
    # Imported lazily: the registry pulls in the plugin runtime client.
    from src.features.notification_providers import (
        registry,  # pylint: disable=import-outside-toplevel
    )

    provider = (await registry.get_notification_providers(db)).get(provider_id)
    return resolve_profile(provider) if provider is not None else None


async def _transport(db: AsyncSession, provider_id: str) -> str | None:
    return await db.scalar(
        select(PluginNotificationProviderRegistration.transport).where(
            PluginNotificationProviderRegistration.provider_id == provider_id,
            PluginNotificationProviderRegistration.revoked_at.is_(None),
        )
    )


def digest_item(notification: Notification, projection: str) -> DigestItem:
    """Per-item identity plus the text the destination is allowed to see."""
    message = notification_message(notification, projection=projection)
    return DigestItem(
        id=notification.id,
        kind=notification.kind,
        event_type=notification.event_type,
        media_type=message.media_type,
        media_id=message.media_id,
        event_at=notification.event_at,
        title=message.title,
        body=message.body,
    )


async def seal_due_windows(db: AsyncSession, *, now: int | None = None, limit: int = 5) -> int:
    """Plan every due window once; concurrent workers skip rows another worker holds."""
    now = int(time.time()) if now is None else now
    sealed = 0
    for _ in range(limit):
        window = await db.scalar(
            select(NotificationAggregationWindow)
            .where(
                NotificationAggregationWindow.status.in_(("open", "full")),
                NotificationAggregationWindow.window_end <= now,
            )
            .order_by(NotificationAggregationWindow.window_end, NotificationAggregationWindow.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if window is None:
            break
        await _seal(db, window, now)
        await db.commit()
        sealed += 1
    return sealed


async def _seal(db: AsyncSession, window: NotificationAggregationWindow, now: int) -> None:
    rows = (
        await db.execute(
            select(NotificationDelivery, Notification)
            .join(Notification, Notification.id == NotificationDelivery.notification_id)
            .where(
                NotificationDelivery.window_id == window.id,
                NotificationDelivery.status == "aggregating",
            )
            .with_for_update(of=NotificationDelivery)
        )
    ).all()
    profile = await live_profile(db, window.provider_id)
    base = await item_link_base(db, profile, window.destination_id)
    newest: dict[UUID, int] = {}
    for _, notice in rows:
        if notice.story_id is not None:
            newest[notice.story_id] = max(
                newest.get(notice.story_id, 0), notice.story_revision or 0
            )
    members: dict[UUID, tuple[NotificationDelivery, DigestItem]] = {}
    for delivery, notice in rows:
        if notice.story_id is not None and (notice.story_revision or 0) < newest[notice.story_id]:
            # A mutable story keeps only its newest revision inside one window.
            delivery.status = "suppressed"
            delivery.last_error = "superseded"
            continue
        members[notice.id] = (
            delivery,
            _linked(digest_item(notice, delivery.projection), notice, base),
        )
    limits = provider_limits(
        window.provider_id, await _transport(db, window.provider_id), profile=profile
    )
    chunks = chunk_items([item for _, item in members.values()], limits)
    for index, chunk in enumerate(chunks):
        batch_id = uuid5(window.id, f"chunk:{index}")
        for position, item in enumerate(chunk):
            delivery = members[item.id][0]
            delivery.batch_id = batch_id
            delivery.batch_index = index
            if position == 0:
                delivery.status = "pending"
                delivery.next_attempt_at = window.window_end
    window.status = "sealed"
    window.sealed_at = now
    await db.flush()


async def recover_orphans(db: AsyncSession, *, limit: int = 20) -> int:
    """Re-lead sealed chunks whose lead was ended by a bulk policy change or a crash."""
    lead = NotificationDelivery.__table__.alias("lead")
    batch_ids = list(
        await db.scalars(
            select(NotificationDelivery.batch_id)
            .where(
                NotificationDelivery.status == "aggregating",
                NotificationDelivery.batch_id.is_not(None),
                ~exists().where(
                    and_(
                        lead.c.batch_id == NotificationDelivery.batch_id,
                        lead.c.status.in_(LIVE_LEAD_STATUSES),
                    )
                ),
            )
            .distinct()
            .limit(limit)
        )
    )
    promoted = 0
    for batch_id in filter(None, batch_ids):
        promoted += int(await promote_next(db, batch_id))
    await db.commit()
    return promoted


async def promote_next(db: AsyncSession, batch_id: UUID) -> bool:
    """Make the earliest remaining member (event time, notification ID) the new lead."""
    next_id = await db.scalar(
        select(NotificationDelivery.id)
        .join(Notification, Notification.id == NotificationDelivery.notification_id)
        .where(
            NotificationDelivery.batch_id == batch_id, NotificationDelivery.status == "aggregating"
        )
        .order_by(Notification.event_at, Notification.id)
        .limit(1)
        .with_for_update(of=NotificationDelivery, skip_locked=True)
    )
    if next_id is None:
        return False
    await db.execute(
        update(NotificationDelivery)
        .where(NotificationDelivery.id == next_id)
        .values(
            status="pending", next_attempt_at=int(time.time()), claim_token=None, lease_until=None
        )
    )
    return True


async def purge_sealed(db: AsyncSession, *, now: int | None = None) -> int:
    """Bound stored state: drop windows sealed long ago (deliveries just lose the link)."""
    cutoff = (int(time.time()) if now is None else now) - SEALED_WINDOW_RETENTION_SECONDS
    result = await db.execute(
        delete(NotificationAggregationWindow).where(
            NotificationAggregationWindow.status == "sealed",
            NotificationAggregationWindow.sealed_at < cutoff,
            ~exists().where(
                and_(
                    NotificationDelivery.window_id == NotificationAggregationWindow.id,
                    NotificationDelivery.status.in_(ACTIVE_DELIVERY_STATUSES),
                )
            ),
        )
    )
    await db.commit()
    return int(getattr(result, "rowcount", 0) or 0)


async def maintain(db: AsyncSession) -> None:
    """Seal due windows, repair headless chunks and prune; safe to run from any worker."""
    await seal_due_windows(db)
    await recover_orphans(db)
    await purge_sealed(db)


async def settle_finished_lead(
    db: AsyncSession, delivery_id: UUID, status: str, error: str | None
) -> None:
    """Called when a lead reaches a terminal state without being sent."""
    batch_id = await db.scalar(
        select(NotificationDelivery.batch_id).where(NotificationDelivery.id == delivery_id)
    )
    if batch_id is None or status == "retry_wait":
        return
    if status == "failed_permanent" or (status == "suppressed" and error in DESTINATION_FAILURES):
        await db.execute(
            update(NotificationDelivery)
            .where(
                NotificationDelivery.batch_id == batch_id,
                NotificationDelivery.status == "aggregating",
            )
            .values(status=status, last_error=error)
        )
        return
    await promote_next(db, batch_id)


@dataclass(frozen=True)
class BatchPlan:
    """The members actually included in one physical message, lead first."""

    included: tuple[UUID, ...]
    message: NotificationMessage


async def _member_verdict(
    db: AsyncSession,
    *,
    delivery: NotificationDelivery,
    notice: Notification | None,
    endpoint: NotificationDestination,
    preferences: dict,
    now: int,
) -> tuple[str, str | None] | None:
    if notice is None or notice.deleted_at is not None:
        return "cancelled", None
    if not await source_delivery_allowed(db, notice):
        return "suppressed", "source_revoked"
    projection = await delivery_projection(db, notice, endpoint, preferences)
    if (
        endpoint.provider_id != INBOX_PROVIDER
        and decide_for(notice, endpoint, preferences, now=now).action == "mute"
    ):
        return "suppressed", "muted"
    if projection != delivery.projection or (
        notice.expires_at is not None and notice.expires_at <= now
    ):
        return "suppressed", "routing_changed"
    return None


async def _compose(
    db: AsyncSession,
    lead: NotificationDelivery,
    lead_notice: Notification,
    items: list[DigestItem],
    *,
    base: NotificationMessage,
    transport: str | None,
    profile: ResolvedProfile | None,
) -> NotificationMessage:
    limits = provider_limits(lead.provider_id, transport, profile=profile)
    parts = int(
        await db.scalar(
            select(func.count(func.distinct(NotificationDelivery.batch_id))).where(  # pylint: disable=not-callable
                NotificationDelivery.window_id == lead.window_id,
                NotificationDelivery.batch_id.is_not(None),
            )
        )
        or 1
    )
    label = LABELS.get(lead_notice.event_type, "new notifications")
    title, body = compose_digest(
        items,
        limits=limits,
        part=(lead.batch_index or 0) + 1,
        parts=parts,
        title_of=lambda count: f"{count} {label}",
    )
    return replace(
        base,
        title=title,
        body=body,
        event_at=max(item.event_at for item in items),
        items=tuple(sorted(items, key=lambda item: (item.event_at, str(item.id)))),
        batch_id=lead.batch_id,
    )


async def item_link_base(
    db: AsyncSession, profile: ResolvedProfile | None, destination_id: UUID | None
) -> str | None:
    """Origin for item links, only when the provider declared canonical link support."""
    if profile is None or not (profile.canonical and profile.capabilities.links):
        return None
    endpoint = await db.get(NotificationDestination, destination_id) if destination_id else None
    if endpoint is None or endpoint.user_id is None:
        return None
    return await notification_url(db, endpoint.user_id, endpoint) or None


def story_url(base: str | None, notice: Notification) -> str | None:
    """Signed-in story page of an item; None when links are unsupported or there is no story."""
    if not base or notice.story_id is None:
        return None
    return f"{base.rstrip('/')}/notifications/stories/{notice.story_id}"


def _linked(item: DigestItem, notice: Notification, base: str | None) -> DigestItem:
    url = story_url(base, notice)
    return replace(item, url=url) if url else item


async def plan_batch(
    db: AsyncSession,
    lead: NotificationDelivery,
    lead_notice: Notification,
    endpoint: NotificationDestination,
    preferences: dict,
    *,
    base: NotificationMessage,
    transport: str | None,
    profile: ResolvedProfile | None = None,
    link_base: str | None = None,
) -> BatchPlan:
    """Revalidate every member at dispatch time and compose the chunk's single message."""
    now = int(time.time())
    rows = (
        await db.execute(
            select(NotificationDelivery, Notification)
            .join(Notification, Notification.id == NotificationDelivery.notification_id)
            .where(
                NotificationDelivery.batch_id == lead.batch_id,
                NotificationDelivery.status == "aggregating",
                NotificationDelivery.id != lead.id,
            )
            .with_for_update(of=NotificationDelivery)
        )
    ).all()
    items = [_linked(digest_item(lead_notice, lead.projection), lead_notice, link_base)]
    included = [lead.id]
    for delivery, notice in rows:
        verdict = await _member_verdict(
            db,
            delivery=delivery,
            notice=notice,
            endpoint=endpoint,
            preferences=preferences,
            now=now,
        )
        if verdict is not None:
            delivery.status, delivery.last_error = verdict
            continue
        items.append(_linked(digest_item(notice, delivery.projection), notice, link_base))
        included.append(delivery.id)
    if len(items) == 1:
        return BatchPlan(tuple(included), base)
    return BatchPlan(
        tuple(included),
        await _compose(
            db, lead, lead_notice, items, base=base, transport=transport, profile=profile
        ),
    )


async def settle_batch(
    db: AsyncSession, lead_id: UUID, included: tuple[UUID, ...], state: str, error: str | None
) -> None:
    """Apply the lead's attempt outcome to the rest of its chunk, in the caller's transaction."""
    if state == "sent":
        await mark_members_sent(db, lead_id, included)
    elif state == "failed_permanent":
        await settle_finished_lead(db, lead_id, state, error)


async def mark_members_sent(db: AsyncSession, lead_id: UUID, included: tuple[UUID, ...]) -> None:
    """Same transaction as the lead's own `sent` transition."""
    others = [value for value in included if value != lead_id]
    if others:
        await db.execute(
            update(NotificationDelivery)
            .where(
                NotificationDelivery.id.in_(others), NotificationDelivery.status == "aggregating"
            )
            .values(status="sent", last_error=None)
        )
