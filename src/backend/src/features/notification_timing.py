"""Glue between stored notifications/preferences and the pure timing policy."""

from __future__ import annotations

from typing import Any

from src.core.config import settings
from src.database.models.notification import Notification
from src.database.models.notification_destination import NotificationDestination
from src.features.notification_policy import INBOX_PROVIDER, route_choice
from src.features.notification_timing_policy import (
    ServerTimingPolicy,
    TimingDecision,
    TimingFacts,
    category_of,
    decide,
)


def server_timing_policy() -> ServerTimingPolicy:
    return ServerTimingPolicy(
        enabled=settings.NOTIFICATION_TIMING_ENABLED,
        urgent_bypass=settings.NOTIFICATION_URGENT_BYPASS_ENABLED,
    )


def timing_facts(
    notification: Notification, destination: NotificationDestination, preferences: dict[str, Any]
) -> TimingFacts:
    """Facts only; the producer's urgency request is a request, never an authority."""
    return TimingFacts(
        event_type=notification.event_type,
        purpose=notification.purpose,
        category=category_of(notification.event_type, notification.source_installation_id),
        destination_id=str(destination.id),
        inbox=destination.provider_id == INBOX_PROVIDER,
        route_urgency=route_choice(notification.event_type, str(destination.id), preferences)[
            "urgency"
        ],
        severity=notification.severity,
        plugin_urgency=(
            notification.requested_urgency if notification.source_installation_id else "normal"
        ),
        kind=notification.kind,
        group_key=notification.group_key,
        # Legacy rows derive group_key from their own ID; only producer hints can group.
        has_group_hint=notification.source_installation_id is not None
        and notification.event_type != "plugin.notice",
    )


def decide_for(
    notification: Notification,
    destination: NotificationDestination,
    preferences: dict[str, Any],
    *,
    now: int,
) -> TimingDecision:
    return decide(
        timing_facts(notification, destination, preferences),
        preferences.get("notification_timing"),
        now=now,
        server=server_timing_policy(),
    )
