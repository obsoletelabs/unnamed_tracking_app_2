"""Host mapping of the canonical notification contract onto existing records.

Pure functions and tables only: no persistence, provider I/O or routing. Trust,
consent, grants, ownership and dispatch-time revalidation stay in the existing
policy, destination and delivery modules. Downstream story, aggregation,
renderer and dispatcher work builds on these definitions.
"""

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Literal, Protocol
from uuid import UUID

from src.database.models.notification import Notification
from src.features.notification_inbox import retention_policy
from src.features.notification_providers.base import notification_message
from src.plugin_api.notification_contracts import (
    NotificationEventEmission,
    NotificationTypeRegistration,
)
from src.plugin_api.notification_story_contracts import (
    LINE_CONTROL_CHARACTERS,
    NOTIFICATION_CONTRACT_VERSION,
    TEXT_CONTROL_CHARACTERS,
    BooleanFactValue,
    CanonicalNotificationEvent,
    ContentFact,
    ContentImage,
    HostMediaImage,
    ImageCapabilities,
    MoneyFactValue,
    NotificationContent,
    NotificationProviderCapabilities,
    StoryLifecycleDeclaration,
    StoryPhase,
    TextBlock,
    TimestampFactValue,
)

if TYPE_CHECKING:  # the controller persists stories and therefore imports this module
    from src.features.notification_controller import NotificationDraft

# Host-reserved one-shot interpretations never join, revise or aggregate into stories.
STORY_PURPOSES = frozenset({"standard", "security"})
PUBLIC_PROJECTIONS = frozenset({"public_release", "media_shared"})
SINGLE_ITEM_PHASE = "posted"
LEGACY_BODY_LIMIT = 10_000


class ItemState(StrEnum):
    """Inbox/history state of one story item (an existing Notification row)."""

    UNREAD = "unread"
    READ = "read"
    DISMISSED = "dismissed"
    EXPIRED = "expired"
    DELETED = "deleted"


class StoryItem(Protocol):
    """Read-only view satisfied by the Notification model."""

    @property
    def read_at(self) -> int | None: ...
    @property
    def dismissed_at(self) -> int | None: ...
    @property
    def deleted_at(self) -> int | None: ...
    @property
    def expires_at(self) -> int | None: ...
    @property
    def event_at(self) -> int: ...


@dataclass(frozen=True)
class ItemOperation:
    sources: frozenset[ItemState]
    target: ItemState
    lifecycle_status: str


_VISIBLE = frozenset({ItemState.UNREAD, ItemState.READ})
# Existing owner routes, retention job and deletion; dismissal is not reversible.
INBOX_OPERATIONS: Mapping[str, ItemOperation] = {
    "read": ItemOperation(_VISIBLE, ItemState.READ, "read"),
    "unread": ItemOperation(_VISIBLE, ItemState.UNREAD, "unread"),
    "dismiss": ItemOperation(_VISIBLE, ItemState.DISMISSED, "dismissed"),
    "expire": ItemOperation(_VISIBLE | {ItemState.DISMISSED}, ItemState.EXPIRED, "expired"),
    "delete": ItemOperation(
        frozenset(ItemState) - {ItemState.DELETED}, ItemState.DELETED, "deleted"
    ),
}

# Existing dispatcher states. Inbox work is recorded directly as sent.
DELIVERY_INITIAL_STATUSES = frozenset({"pending", "sent"})
DELIVERY_TRANSITIONS: Mapping[str, frozenset[str]] = {
    "pending": frozenset({"processing", "suppressed", "cancelled"}),
    # processing -> processing is an expired lease reclaimed under a new claim token.
    "processing": frozenset(
        {"processing", "sent", "retry_wait", "failed_permanent", "suppressed", "cancelled"}
    ),
    "retry_wait": frozenset({"processing", "suppressed", "cancelled"}),
    "sent": frozenset(),
    "failed_permanent": frozenset(),
    "suppressed": frozenset(),
    "cancelled": frozenset(),
}
ATTEMPT_TRANSITIONS: Mapping[str, frozenset[str]] = {
    "processing": frozenset({"sent", "failed", "unknown"}),
    "sent": frozenset(),
    "failed": frozenset(),
    "unknown": frozenset(),
}
AttemptOperation = Literal["send", "edit", "follow_up"]


def item_state(item: StoryItem, *, retention_cutoff: int | None = None) -> ItemState:
    """Precedence is deleted, retention-expired, dismissed, read, then unread."""
    if item.deleted_at is not None:
        return ItemState.DELETED
    if retention_cutoff is not None and item.event_at < retention_cutoff:
        return ItemState.EXPIRED
    if item.dismissed_at is not None:
        return ItemState.DISMISSED
    return ItemState.READ if item.read_at is not None else ItemState.UNREAD


def inbox_retention_cutoff(preferences: dict[str, Any], now: int) -> int | None:
    """Reuse the existing inbox retention policy; zero effective days means keep forever."""
    days = retention_policy(preferences)["effective_days"]
    return now - days * 86400 if days else None


def revision_eligible(item: StoryItem, *, now: int, retention_cutoff: int | None) -> bool:
    """Only an unread, undismissed, retained and still-valid item may change in place."""
    return item_state(item, retention_cutoff=retention_cutoff) == ItemState.UNREAD and (
        item.expires_at is None or item.expires_at > now
    )


class RevisionDecision(StrEnum):
    CREATE_STORY = "create_story"
    UPDATE_IN_PLACE = "update_in_place"
    NEW_LINKED_ITEM = "new_linked_item"
    REJECT_TRANSITION = "reject_transition"
    REJECT_CLOSED = "reject_closed"


@dataclass(frozen=True)
class StoryHead:
    """Current story phase/revision and its newest item, read under the story row lock."""

    phase: str
    revision: int
    item: StoryItem


def single_item_lifecycle(event_type: str) -> StoryLifecycleDeclaration:
    """Events without a story reference: one item per event, never revised."""
    return StoryLifecycleDeclaration(
        contract_version=NOTIFICATION_CONTRACT_VERSION,
        event_type=event_type,
        phases=(StoryPhase(id=SINGLE_ITEM_PHASE, label="Posted"),),
        initial_phases=(SINGLE_ITEM_PHASE,),
    )


def decide_revision(
    lifecycle: StoryLifecycleDeclaration,
    head: StoryHead | None,
    phase: str,
    *,
    now: int,
    retention_cutoff: int | None,
) -> RevisionDecision:
    """Apply the transition table after receipt deduplication has accepted a new event."""
    if lifecycle.phase(phase) is None:
        return RevisionDecision.REJECT_TRANSITION
    if head is None:
        return (
            RevisionDecision.CREATE_STORY
            if phase in lifecycle.initial_phases
            else RevisionDecision.REJECT_TRANSITION
        )
    current = lifecycle.phase(head.phase)
    # Terminal phases have no declared outgoing edges, so they always land here.
    if current is None or not lifecycle.allows(head.phase, phase):
        return (
            RevisionDecision.REJECT_CLOSED
            if current is not None and current.terminal
            else RevisionDecision.REJECT_TRANSITION
        )
    return (
        RevisionDecision.UPDATE_IN_PLACE
        if revision_eligible(head.item, now=now, retention_cutoff=retention_cutoff)
        else RevisionDecision.NEW_LINKED_ITEM
    )


def reconciliation_operation(
    capabilities: NotificationProviderCapabilities,
    *,
    previously_delivered: bool,
    remote_reference_current: bool,
    new_linked_item: bool,
) -> AttemptOperation:
    """Explicit capability fallback for a later revision; policy may still create no work."""
    if not previously_delivered:
        return "send"
    if (
        not new_linked_item
        and remote_reference_current
        and capabilities.message_updates in {"edit", "replace"}
    ):
        return "edit"
    return "follow_up"


def source_receipt_key(source: str, event_type: str, event_id: str) -> str:
    """Payload-free receipt identity for registered plugin sources (Plugin API 1.1.2)."""
    identity = hashlib.sha256(f"{source}:{event_type}".encode()).hexdigest()
    return f"src:{identity}:{event_id}"


def _line(value: str, fallback: str) -> str:
    text = LINE_CONTROL_CHARACTERS.sub(" ", value)
    return text if text.strip() else fallback


def _text(value: str, fallback: str) -> str:
    text = TEXT_CONTROL_CHARACTERS.sub(" ", value.replace("\r\n", "\n"))
    return text if text.strip() else fallback


def _legacy_content(
    title: str, body: str, artwork: HostMediaImage | None, expires_at: int | None = None
) -> NotificationContent:
    title = _line(title, "Notification")[:500]
    return NotificationContent(
        title=title,
        summary=_text(body, title),
        images=(ContentImage(source=artwork, alt=title[:300]),) if artwork else (),
        expires_at=expires_at,
    )


def _artwork(media_type: str, media_id: UUID, poster_url: str | None) -> HostMediaImage | None:
    """Raw provider poster URLs never leave the host; the protected media route serves them."""
    if not poster_url or not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", media_type):
        return None
    return HostMediaImage(kind="host_media", media_type=media_type, media_id=media_id)


def content_from_draft(draft: "NotificationDraft") -> NotificationContent:
    """Existing host producers (`NotificationDraft`) expressed as canonical content."""
    return _legacy_content(
        draft.title, draft.body, _artwork(draft.entity_type, draft.entity_id, draft.poster_url)
    )


def content_from_notification(
    notification: Notification, projection: str = "canonical"
) -> NotificationContent:
    """Existing rows and approved projections, using the dispatcher's own text selection."""
    if notification.deleted_at is not None:
        raise ValueError("Deleted notifications retain no content")
    if projection != "canonical" and projection not in PUBLIC_PROJECTIONS:
        raise ValueError("Unknown notification projection")
    message = notification_message(notification, projection=projection)
    artwork = (
        None
        if projection in PUBLIC_PROJECTIONS
        else _artwork(notification.media_type, notification.media_id, notification.poster_url)
    )
    return _legacy_content(message.title, message.body, artwork)


def emission_event(
    emission: NotificationEventEmission, definition: NotificationTypeRegistration
) -> CanonicalNotificationEvent:
    """Plugin API 1.1.2 typed-template emission expressed as a canonical v1 event.

    Parameters are already interpolated by the host-reviewed templates, so the
    content carries exactly the persisted title/body and no duplicate facts.
    """
    data = dict(emission.data)
    title = definition.title_template.format_map(data)
    body = definition.body_template.format_map(data)
    if len(title) > 500 or len(body) > LEGACY_BODY_LIMIT:
        raise ValueError("Notification event exceeds its bounds")
    return CanonicalNotificationEvent(
        contract_version=NOTIFICATION_CONTRACT_VERSION,
        event_type=emission.event_type,
        event_id=emission.dedupe_key,
        occurred_at=emission.occurred_at,
        grouping_key=emission.group_key,
        content=_legacy_content(title, body, None),
    )


def _fact_text(fact: ContentFact) -> str:
    value = fact.value
    if isinstance(value, BooleanFactValue):
        shown = "Yes" if value.value else "No"
    elif isinstance(value, MoneyFactValue):
        shown = f"{value.amount} {value.currency}"
    elif isinstance(value, TimestampFactValue):
        shown = datetime.fromtimestamp(value.value, UTC).strftime("%Y-%m-%d %H:%M UTC")
    else:
        shown = str(value.value)
    return f"{fact.label}: {shown}"


def legacy_text(content: NotificationContent) -> tuple[str, str]:
    """Deterministic title/body for renderers that negotiated no canonical version.

    Images and actions are omitted: legacy DTOs never carried producer URLs, and
    the host keeps supplying its own notification link where the DTO has one.
    """
    parts = [content.summary]
    parts.extend(
        block.text if isinstance(block, TextBlock) else block.markdown for block in content.blocks
    )
    if content.facts:
        parts.append("\n".join(_fact_text(fact) for fact in content.facts))
    body = "\n\n".join(parts)
    if len(body) > LEGACY_BODY_LIMIT:
        body = body[: LEGACY_BODY_LIMIT - 1] + "\u2026"
    return content.title, body


@dataclass(frozen=True)
class ProviderCapabilityProfile:
    """Discovered presentation profile; authorization is always rechecked at dispatch."""

    renderer: Literal["legacy", "canonical"]
    capabilities: NotificationProviderCapabilities
    critical_supported: bool = False
    generic_preview_only: bool = False


def _baseline(**changes: Any) -> NotificationProviderCapabilities:
    return NotificationProviderCapabilities(
        contract_version=NOTIFICATION_CONTRACT_VERSION, **changes
    )


# Current behaviour of each existing transport, delivered through `legacy_text`.
BUILTIN_PROFILES: Mapping[str, ProviderCapabilityProfile] = {
    "core.inbox": ProviderCapabilityProfile(
        "legacy", _baseline(images=ImageCapabilities(max_images=1, proxied_urls=True))
    ),
    "core.smtp": ProviderCapabilityProfile(
        "legacy", _baseline(links=True), critical_supported=True
    ),
    "core.browser": ProviderCapabilityProfile(
        "legacy",
        # A tag replaces the earlier alert on the device, so a later story revision is a
        # `replace` without any per-message reference from the push service.
        _baseline(max_payload_bytes=4096, message_updates="replace"),
        generic_preview_only=True,
    ),
}
TRANSPORT_PROFILES: Mapping[str, ProviderCapabilityProfile] = {
    "legacy": ProviderCapabilityProfile("legacy", _baseline()),
    "discord_webhook": ProviderCapabilityProfile(
        "legacy",
        _baseline(rich_embeds=True, links=True, max_title_chars=256, max_text_chars=4096),
    ),
    "plugin": ProviderCapabilityProfile("legacy", _baseline(links=True)),
}


def provider_capability_profile(
    provider_id: str,
    *,
    transport: str | None = None,
    critical_supported: bool = False,
    declared: NotificationProviderCapabilities | None = None,
) -> ProviderCapabilityProfile:
    """Discover a provider's profile; undeclared plugin providers keep their legacy baseline."""
    if provider_id in BUILTIN_PROFILES:
        return BUILTIN_PROFILES[provider_id]
    baseline = TRANSPORT_PROFILES.get(transport or "")
    if baseline is None:
        raise ValueError("Unknown notification provider transport")
    if declared is not None:
        if transport != "plugin":
            raise ValueError("Only generic plugin providers can declare canonical capabilities")
        return ProviderCapabilityProfile("canonical", declared, critical_supported)
    return ProviderCapabilityProfile("legacy", baseline.capabilities, critical_supported)


@dataclass(frozen=True)
class LegacyAdapter:
    introduced_in: str
    documents: tuple[str, ...]
    adapter: str


# Every existing notification gateway/runtime surface and its canonical mapping.
LEGACY_ADAPTERS: Mapping[str, LegacyAdapter] = {
    "notifications.send": LegacyAdapter("1.0.0", ("title/body",), "content_from_draft"),
    "notifications.emit": LegacyAdapter("1.1.2", ("NotificationEventEmission",), "emission_event"),
    "notification_sources.register": LegacyAdapter(
        "1.1.2", ("NotificationTypeRegistration",), "single_item_lifecycle"
    ),
    "notification_sources.unregister": LegacyAdapter("1.1.2", ("event_type",), "unchanged"),
    "notification_providers.register": LegacyAdapter(
        "1.1.2", ("NotificationProviderRegistration",), "provider_capability_profile"
    ),
    "notification_providers.unregister": LegacyAdapter("1.1.2", ("provider_id",), "unchanged"),
    "notification-deliveries": LegacyAdapter(
        "1.1.2",
        ("NotificationDeliveryRepresentation", "PluginNotificationContent"),
        "legacy_text",
    ),
    "notification-layouts": LegacyAdapter("1.1.2", ("NotificationFieldLayout",), "legacy_text"),
    "notifications.lifecycle.poll": LegacyAdapter(
        "1.1.4", ("NotificationLifecyclePage",), "INBOX_OPERATIONS/DELIVERY_TRANSITIONS"
    ),
    "notification_destinations.status": LegacyAdapter(
        "1.1.11", ("NotificationDestinationStatusPage",), "unchanged"
    ),
    # Plugin API 1.1.14: native canonical surfaces, so no legacy mapping is involved.
    "notifications.publish": LegacyAdapter("1.1.14", ("CanonicalNotificationEvent",), "unchanged"),
    "notifications.contract": LegacyAdapter("1.1.14", ("ContractNegotiationRequest",), "unchanged"),
    "notification-story-actions": LegacyAdapter("1.1.14", ("PluginActionTarget",), "unchanged"),
}
