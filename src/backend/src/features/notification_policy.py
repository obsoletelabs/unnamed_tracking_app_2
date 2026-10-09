"""Notification disclosure policy shared by acceptance and dispatch."""

from enum import IntEnum
from typing import Any

from src.core.config import settings
from src.database.models.notification import Notification
from src.database.models.notification_destination import NotificationDestination

INBOX_PROVIDER = "core.inbox"
MEDIA_KINDS = {
    "episode_aired",
    "season_started",
    "sequel_announced",
    "movie_released",
    "game_released",
    "game_sale",
    "game_price_hit",
}


class Trust(IntEnum):
    PUBLIC = 0
    PRIVATE = 1
    SECURE = 2


def effective_trust(destination: NotificationDestination) -> Trust:
    """SECURE requires current host evidence, not a caller's numeric assertion."""
    if destination.provider_id == INBOX_PROVIDER and destination.channel_context == "internal":
        return Trust.SECURE
    if (
        destination.privacy == Trust.PRIVATE
        and destination.verified_revision == destination.revision
        and destination.verification_method
        and destination.verification_revoked_at is None
    ):
        return Trust.SECURE
    return Trust.PRIVATE if destination.privacy == Trust.PRIVATE else Trust.PUBLIC


def preference_enabled(notification: Notification, preferences: dict[str, Any]) -> bool:
    if notification.source_installation_id is None and not preferences.get(
        f"notify_{notification.kind}", True
    ):
        return False
    return preferences.get("notification_types", {}).get(notification.event_type, True)


def route_choice(
    event_type: str, destination_id: str, preferences: dict[str, Any]
) -> dict[str, Any]:
    """Resolve a personal choice, defaulting to normal; urgency grants no trust."""
    return (
        preferences.get("notification_routes", {})
        .get(event_type, {})
        .get(destination_id, {"enabled": True, "urgency": "normal"})
    )


# Separate early denials keep trust, preferences and disclosure decisions explicit.
# pylint: disable-next=too-many-return-statements
def select_projection(
    notification: Notification,
    destination: NotificationDestination,
    preferences: dict[str, Any],
) -> str | None:
    """Return an approved representation or deny. Provider availability is a separate gate."""
    if (
        not destination.enabled
        or not destination.active
        or notification.deleted_at is not None
        or notification.user_id != destination.user_id
        or not preference_enabled(notification, preferences)
    ):
        return None
    if preferences.get("notification_destinations", {}).get(str(destination.id)) is False:
        return None
    # Possession challenges must not be copied to the already authenticated inbox.
    if notification.purpose in {"verification", "test"} and (
        notification.media_type != "notification_destination"
        or notification.media_id != destination.id
    ):
        return None
    if not route_choice(notification.event_type, str(destination.id), preferences)["enabled"]:
        return None
    if destination.provider_id in {
        entry.strip() for entry in settings.NOTIFICATION_BLOCKED_PROVIDERS.split(",")
    } or notification.event_type in {
        entry.strip() for entry in settings.NOTIFICATION_BLOCKED_TYPES.split(",")
    }:
        return None
    required_trust = max(notification.required_trust, settings.NOTIFICATION_MINIMUM_TRUST)
    trust = effective_trust(destination)
    if notification.purpose == "recovery" and (
        destination.channel_context != "external"
        or not destination.recovery_allowed
        or trust != Trust.SECURE
    ):
        return None
    # Phase 1 legacy renderers cannot receive sensitive content. A separate
    # high-risk grant and protected provider contract will be added in Phase 4A.
    if destination.installation_id is not None and required_trust == Trust.SECURE:
        return None
    if trust >= required_trust:
        return "canonical"
    public_release = all(
        (
            trust == Trust.PUBLIC,
            notification.kind in MEDIA_KINDS,
            notification.purpose == "standard",
            notification.required_trust == Trust.PRIVATE,
            settings.NOTIFICATION_MINIMUM_TRUST == Trust.PUBLIC,
            notification.public_title is not None,
            notification.public_body is not None,
        )
    )
    if public_release:
        # Shared legacy endpoints have no host-owned endpoint revision; never
        # interpret their configuration as per-webhook richer disclosure consent.
        if (
            destination.kind != "legacy_webhook"
            and destination.media_consent_revision == destination.revision
            and destination.media_consent_at is not None
        ):
            return "media_shared"
        return "public_release"
    return None
