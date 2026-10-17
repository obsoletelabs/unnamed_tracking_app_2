"""Story reconciliation: which physical operation presents a revision at one destination.

A delivery presents one story revision. When an earlier revision already reached the same
destination revision, the dispatcher edits that remote message when the provider can, otherwise it
sends a follow-up (subject to the configured follow-up rule). The remote reference is opaque,
host-held and keyed by destination revision, so a changed destination is never edited through an
old reference. Nothing here promises exactly-once delivery: a crash between the provider call and
the commit re-sends (idempotently where the provider honours the stable idempotency key).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import exists, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import settings
from src.database.models.notification import Notification
from src.database.models.notification_delivery import NotificationDelivery
from src.database.models.notification_remote_reference import NotificationRemoteReference
from src.features.notification_audit import change_deliveries
from src.features.notification_contract import reconciliation_operation
from src.features.notification_providers.capabilities import ResolvedProfile

UNSENT = ("pending", "retry_wait", "aggregating")
IMPORTANT_SEVERITIES = frozenset({"warning", "error", "critical"})


@dataclass(frozen=True)
class ReconcilePlan:
    operation: str
    reference: NotificationRemoteReference | None
    fallbacks: tuple[str, ...] = field(default=())
    # Terminal outcome decided without any provider call: (status, reason).
    outcome: tuple[str, str] | None = None


async def supersede_unsent(
    db: AsyncSession, notification_id: UUID, destination_id: UUID, revision: int
) -> None:
    """Unsent work for an older revision is replaced by the newer revision's obligation."""
    older = (
        NotificationDelivery.destination_id == destination_id,
        NotificationDelivery.story_revision < revision,
        NotificationDelivery.status.in_(UNSENT),
    )
    replace_work = (
        update(NotificationDelivery)
        .where(NotificationDelivery.notification_id == notification_id, *older)
        .values(status="suppressed", last_error="superseded")
    )
    await change_deliveries(db, replace_work)


async def has_prior_presentation(
    db: AsyncSession, notification_id: UUID, destination_id: UUID, revision: int
) -> bool:
    return bool(
        await db.scalar(
            select(
                exists().where(
                    NotificationDelivery.notification_id == notification_id,
                    NotificationDelivery.destination_id == destination_id,
                    NotificationDelivery.story_revision < revision,
                    NotificationDelivery.status.in_(("sent", "processing")),
                )
            )
        )
    )


async def load_reference(
    db: AsyncSession, delivery: NotificationDelivery, *, lock: bool = False
) -> NotificationRemoteReference | None:
    statement = select(NotificationRemoteReference).where(
        NotificationRemoteReference.notification_id == delivery.notification_id,
        NotificationRemoteReference.destination_id == delivery.destination_id,
        NotificationRemoteReference.destination_revision == delivery.destination_revision,
    )
    if lock:
        statement = statement.with_for_update()
    return await db.scalar(statement.execution_options(populate_existing=True))


async def _revision_in_flight(db: AsyncSession, delivery: NotificationDelivery, now: int) -> bool:
    """An older revision is mid-send: its reference is not committed yet, so wait for it."""
    return bool(
        await db.scalar(
            select(
                exists().where(
                    NotificationDelivery.notification_id == delivery.notification_id,
                    NotificationDelivery.destination_id == delivery.destination_id,
                    NotificationDelivery.story_revision < delivery.story_revision,
                    NotificationDelivery.status == "processing",
                    NotificationDelivery.lease_until > now,
                )
            )
        )
    )


def _fallback_reason(profile: ResolvedProfile, ref: NotificationRemoteReference | None) -> str:
    if not profile.can_edit:
        return "edit_unsupported"
    if ref is None or ref.remote_ref is None:
        return "reference_unavailable"
    if ref.shared:
        return "shared_message"
    return "edit_failed_earlier"


def _suppressed(notification: Notification, operation: str) -> str | None:
    """The configured rule for revisions of a story the destination already saw."""
    mode = settings.NOTIFICATION_FOLLOW_UPS
    if mode == "none":
        return "revision_policy"
    if (
        operation == "follow_up"
        and mode == "important"
        and notification.severity not in IMPORTANT_SEVERITIES
    ):
        return "follow_up_policy"
    return None


async def plan_operation(
    db: AsyncSession,
    delivery: NotificationDelivery,
    notification: Notification,
    profile: ResolvedProfile,
) -> ReconcilePlan:
    """Choose send, edit or follow-up from persisted state and the live capability profile."""
    ref = await load_reference(db, delivery, lock=True)
    sibling_sent = bool(
        await db.scalar(
            select(
                exists().where(
                    NotificationDelivery.notification_id == delivery.notification_id,
                    NotificationDelivery.destination_id == delivery.destination_id,
                    NotificationDelivery.destination_revision == delivery.destination_revision,
                    NotificationDelivery.story_revision < delivery.story_revision,
                    NotificationDelivery.status == "sent",
                )
            )
        )
    )
    if ref is not None and ref.delivered_revision >= delivery.story_revision > 0:
        # Already presented (for example a retry after the remote step committed).
        return ReconcilePlan("send", ref, ("already_reconciled",), ("sent", "already_reconciled"))
    if delivery.batch_id is not None or not (ref is not None or sibling_sent):
        return ReconcilePlan("send", ref)
    current = bool(ref is not None and ref.state == "current" and ref.remote_ref and not ref.shared)
    operation = reconciliation_operation(
        profile.capabilities,
        previously_delivered=True,
        remote_reference_current=current,
        new_linked_item=False,
    )
    fallbacks = () if operation == "edit" else (_fallback_reason(profile, ref),)
    reason = _suppressed(notification, operation)
    if reason is not None:
        return ReconcilePlan(operation, ref, (*fallbacks, reason), ("suppressed", reason))
    return ReconcilePlan(operation, ref, fallbacks)


async def in_flight(db: AsyncSession, delivery: NotificationDelivery) -> bool:
    return await _revision_in_flight(db, delivery, int(time.time()))


async def mark_edit_failed(db: AsyncSession, reference: NotificationRemoteReference | None) -> None:
    """The provider refused the edit: later revisions use follow-ups for this message."""
    if reference is not None:
        reference.state = "stale"
        reference.edit_failures += 1
        reference.updated_at = int(time.time())
        await db.flush()


async def record_presented(
    db: AsyncSession,
    delivery_ids: tuple[UUID, ...],
    *,
    remote_ref: str | None,
    content_hash: str,
    keep_reference: bool = False,
) -> None:
    """Upsert the reference of every item the successful operation presented."""
    rows = (
        await db.execute(
            select(
                NotificationDelivery.notification_id,
                NotificationDelivery.destination_id,
                NotificationDelivery.destination_revision,
                NotificationDelivery.provider_id,
                NotificationDelivery.story_revision,
                Notification.story_id,
            )
            .join(Notification, Notification.id == NotificationDelivery.notification_id)
            .where(NotificationDelivery.id.in_(delivery_ids))
        )
    ).all()
    now = int(time.time())
    for notification_id, destination_id, destination_revision, provider_id, revision, story in rows:
        if destination_id is None:
            continue
        values = {
            "notification_id": notification_id,
            "story_id": story,
            "destination_id": destination_id,
            "destination_revision": destination_revision,
            "provider_id": provider_id,
            "remote_ref": remote_ref,
            "delivered_revision": revision,
            "content_hash": content_hash,
            "state": "current",
            "shared": len(rows) > 1,
            "edit_failures": 0,
            "created_at": now,
            "updated_at": now,
        }
        # An edit that returns no identifier keeps the one that already addresses the message;
        # a new message without one cannot be edited later, so the old address is dropped.
        keep = {} if keep_reference and remote_ref is None else {"remote_ref": remote_ref}
        await db.execute(
            pg_insert(NotificationRemoteReference)
            .values(**values)
            .on_conflict_do_update(
                constraint="uq_notification_remote_reference",
                set_={
                    **keep,
                    "delivered_revision": revision,
                    "content_hash": content_hash,
                    "state": "current",
                    "shared": len(rows) > 1,
                    "updated_at": now,
                    "story_id": story,
                },
            )
        )


async def story_delivery_diagnostics(db: AsyncSession, user_id: UUID, story_id: UUID) -> list[dict]:
    """Owner-facing view of how each destination received each revision of a story.

    Reports operations, outcomes and fallback codes only: never remote identifiers, endpoint
    configuration or content.
    """
    from src.database.models.notification_destination import (  # pylint: disable=import-outside-toplevel
        NotificationDestination,
    )

    rows = await db.execute(
        select(NotificationDelivery, NotificationDestination.display_name)
        .join(Notification, Notification.id == NotificationDelivery.notification_id)
        .outerjoin(
            NotificationDestination,
            NotificationDestination.id == NotificationDelivery.destination_id,
        )
        .where(Notification.story_id == story_id, Notification.user_id == user_id)
        .order_by(NotificationDelivery.story_revision, NotificationDelivery.provider_id)
    )
    return [
        {
            "revision": delivery.story_revision,
            "provider_id": delivery.provider_id,
            "destination": name,
            "operation": delivery.operation,
            "status": delivery.status,
            "attempts": delivery.attempts,
            "reason": delivery.last_error,
            "fallbacks": [code for code in (delivery.fallback or "").split(",") if code],
        }
        for delivery, name in rows
    ]
