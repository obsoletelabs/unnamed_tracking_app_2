"""Cached deployment-wide delivery backlog; never query the outbox during a scrape."""

import logging

from sqlalchemy import case, func, select
from sqlalchemy.sql.functions import count as sql_count

from src.core.metrics import (
    NOTIFICATION_BACKLOG_UP,
    NOTIFICATION_OLDEST_PENDING,
    NOTIFICATION_PENDING,
)
from src.database.models.notification_delivery import NotificationDelivery
from src.database.session import SessionLocal
from src.features.notification_policy import INBOX_PROVIDER

logger = logging.getLogger(__name__)
_CHANNELS = ("browser", "email", "inbox", "plugin")
_STATES = ("pending", "retry_wait", "processing")


async def refresh_notification_backlog() -> None:
    """Read one aggregate over the indexed queue and publish a bounded snapshot."""
    channel = case(
        (NotificationDelivery.provider_id == "core.browser", "browser"),
        (NotificationDelivery.provider_id == "core.smtp", "email"),
        (NotificationDelivery.provider_id == INBOX_PROVIDER, "inbox"),
        else_="plugin",
    )
    try:
        async with SessionLocal() as db:
            rows = (
                await db.execute(
                    select(
                        channel.label("channel"),
                        NotificationDelivery.status,
                        sql_count(),
                        func.min(NotificationDelivery.next_attempt_at),
                    )
                    .where(NotificationDelivery.status.in_(_STATES))
                    .group_by(channel, NotificationDelivery.status)
                )
            ).all()
    except Exception:  # pylint: disable=broad-exception-caught
        logger.exception("Notification backlog metrics refresh failed")
        NOTIFICATION_BACKLOG_UP.set(0)
        for kind in _CHANNELS:
            NOTIFICATION_OLDEST_PENDING.remove(kind)
            for state in _STATES:
                NOTIFICATION_PENDING.remove(kind, state)
        return

    counts = {(kind, state): 0 for kind in _CHANNELS for state in _STATES}
    oldest = {kind: 0 for kind in _CHANNELS}
    for kind, state, count, timestamp in rows:
        counts[(kind, state)] = int(count)
        if state in {"pending", "retry_wait"} and timestamp is not None:
            current = oldest[kind]
            oldest[kind] = min(current, int(timestamp)) if current else int(timestamp)
    for (kind, state), count in counts.items():
        NOTIFICATION_PENDING.labels(kind, state).set(count)
    for kind, timestamp in oldest.items():
        NOTIFICATION_OLDEST_PENDING.labels(kind).set(timestamp)
    NOTIFICATION_BACKLOG_UP.set(1)
