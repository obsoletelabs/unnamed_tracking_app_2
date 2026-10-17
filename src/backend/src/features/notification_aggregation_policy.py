"""Pure aggregation policy: strategies, deterministic windows, ordering, chunking and rendering.

Nothing here touches the database or the clock; callers pass ``now`` so every boundary is
reproducible with a fake clock. The persistent state lives in ``notification_aggregation``.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from src.features.notification_contract import BUILTIN_PROFILES, TRANSPORT_PROFILES
from src.features.notification_providers.base import DigestItem

Strategy = Literal["immediate", "coalesce", "digest"]

# Bounded state: a window never holds more members than this; a full window is sealed early.
MAX_WINDOW_MEMBERS = 500
# Hard cap on items rendered into one physical message, independent of text limits.
MAX_CHUNK_ITEMS = 50
ITEM_LINE_LIMIT = 200
SEALED_WINDOW_RETENTION_SECONDS = 7 * 86400
DIGEST_PURPOSES = frozenset({"standard"})
# Providers whose payload is a fixed generic preview never carry item text.
GENERIC_PREVIEW_PROVIDERS = frozenset({"core.browser"})


@dataclass(frozen=True)
class AggregationPolicy:
    """Declared per event type; it is data, so it is testable and documented in one place."""

    strategy: Strategy
    window_seconds: int = 0
    # entity: one window per (event type, entity, UTC day of the event);
    # event_type: app-wide per event type; group_key: producer hint within the event type.
    scope: Literal["entity", "event_type", "group_key"] = "event_type"
    urgent_bypass: bool = True
    # Digests close on fixed UTC boundaries; coalescing windows start at the first member.
    aligned: bool = False


IMMEDIATE = AggregationPolicy("immediate")
POLICIES: dict[str, AggregationPolicy] = {
    "media.episode.aired": AggregationPolicy("coalesce", 900, "entity"),
    "media.season.started": AggregationPolicy("coalesce", 900, "entity"),
    "media.sequel.announced": AggregationPolicy("coalesce", 900, "entity"),
    "media.movie.released": AggregationPolicy("coalesce", 900, "entity"),
    "game.released": AggregationPolicy("coalesce", 900, "entity"),
    "game.sale.started": AggregationPolicy("digest", 6 * 3600, "event_type", aligned=True),
    "system.plugin.update": AggregationPolicy("digest", 86400, "event_type", aligned=True),
}
PLUGIN_GROUP_POLICY = AggregationPolicy("coalesce", 300, "group_key")


def policy_for(
    *, event_type: str, kind: str, purpose: str, group_key: str | None, has_group_hint: bool
) -> AggregationPolicy:
    """Resolve the policy; only ordinary notifications are ever eligible for aggregation."""
    if purpose not in DIGEST_PURPOSES:
        return IMMEDIATE
    declared = POLICIES.get(event_type)
    if declared is not None:
        return declared
    if kind == "plugin" and has_group_hint and group_key:
        return PLUGIN_GROUP_POLICY
    return IMMEDIATE


def bypasses(policy: AggregationPolicy, *, requested_urgency: str, severity: str) -> bool:
    """Urgent delivery skips aggregation only when the policy permits it."""
    return policy.urgent_bypass and (requested_urgency == "critical" or severity == "critical")


def scope_key(
    policy: AggregationPolicy, *, event_type: str, media_id: UUID, event_at: int, group_key: str
) -> str:
    """The group identity; distinct entities or release days never share a window."""
    if policy.scope == "entity":
        return f"{event_type}:{media_id}:{event_at // 86400}"[:200]
    if policy.scope == "group_key":
        return f"{event_type}:{group_key}"[:200]
    return event_type[:200]


def window_bounds(policy: AggregationPolicy, now: int) -> tuple[int, int]:
    """Deterministic [start, end) for a window opened at ``now``."""
    if policy.aligned:
        start = now - now % policy.window_seconds
        return start, start + policy.window_seconds
    return now, now + policy.window_seconds


@dataclass(frozen=True)
class ProviderLimits:
    max_items: int
    max_title_chars: int
    max_text_chars: int
    max_payload_bytes: int
    links: bool


def provider_limits(provider_id: str, transport: str | None) -> ProviderLimits:
    """Limits of one composed plain-text message, from the discovered capability profile."""
    profile = BUILTIN_PROFILES.get(provider_id) or TRANSPORT_PROFILES.get(transport or "legacy")
    caps = (profile or TRANSPORT_PROFILES["legacy"]).capabilities
    return ProviderLimits(
        max_items=MAX_CHUNK_ITEMS,
        max_title_chars=caps.max_title_chars,
        max_text_chars=caps.max_text_chars,
        max_payload_bytes=caps.max_payload_bytes,
        links=caps.links,
    )


def order_key(item: DigestItem) -> tuple[int, str]:
    """Occurrence time, then notification ID: the contract's batch order."""
    return item.event_at, str(item.id)


def item_line(item: DigestItem) -> str:
    text = " ".join(item.title.split())
    if len(text) > ITEM_LINE_LIMIT:
        text = text[: ITEM_LINE_LIMIT - 1] + "…"
    return f"- {text}"


def _size(lines: Iterable[str]) -> tuple[int, int]:
    text = "\n".join(lines)
    return len(text), len(text.encode())


def chunk_items(items: Sequence[DigestItem], limits: ProviderLimits) -> list[list[DigestItem]]:
    """Split deterministically; the same input always yields the same chunks.

    Order is (event time, notification id). A chunk closes when adding the next line would
    exceed the item cap or the text/payload budget (reserving room for the part header).
    """
    reserve = 64
    chunks: list[list[DigestItem]] = []
    current: list[DigestItem] = []
    lines: list[str] = []
    for item in sorted(items, key=order_key):
        line = item_line(item)
        chars, size = _size([*lines, line])
        over = (
            len(current) >= limits.max_items
            or chars + reserve > limits.max_text_chars
            or size + reserve > limits.max_payload_bytes
        )
        if current and over:
            chunks.append(current)
            current, lines = [], []
        current.append(item)
        lines.append(line)
    if current:
        chunks.append(current)
    return chunks


def compose_digest(
    items: Sequence[DigestItem],
    *,
    limits: ProviderLimits,
    part: int,
    parts: int,
    title_of: Callable[[int], str] | None = None,
) -> tuple[str, str]:
    """Title and plain-text body for one chunk. Item identity travels in ``items``."""
    ordered = sorted(items, key=order_key)
    count = len(ordered)
    if count == 1:
        title = ordered[0].title
    else:
        title = title_of(count) if title_of else f"{count} new notifications"
    if parts > 1:
        title = f"{title} ({part}/{parts})"
    if len(title) > limits.max_title_chars:
        title = title[: limits.max_title_chars - 1] + "…"
    body = "\n".join(item_line(item) for item in ordered)
    if count == 1:
        body = ordered[0].body or body
    return title, body[: limits.max_text_chars]
