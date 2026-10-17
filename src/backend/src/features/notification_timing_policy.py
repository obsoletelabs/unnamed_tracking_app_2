"""Pure timing policy: category/destination schedules, quiet hours, modes and constrained urgency.

Nothing here touches the database or the wall clock; callers pass ``now`` and the preference
document, so every precedence and DST decision is reproducible with a fake clock. The result is an
explainable :class:`TimingDecision`; it only ever says *when* and *how batched* an already
permitted delivery is sent. Trust, consent, route enablement and server restrictions are decided
earlier by ``notification_policy`` and are never relaxed here.

Precedence, strongest first:

1. Hard gates (not here): disabled route/type/destination/provider, server blocks, trust.
2. Immune traffic: any non-``standard`` purpose (security, recovery, verification, invitation,
   test) and the in-app inbox are delivered immediately whatever the user configured.
3. User rules, most specific first: category + destination, destination, category, default rule.
   Each field (mode, quiet hours, urgency override) comes from the most specific rule that sets it.
4. Account defaults (``notification_timing`` top level), then the event type's own aggregation
   policy from ``notification_aggregation_policy`` (the pre-existing behaviour).
5. Urgency: a request (user's route choice, host severity, plugin request) bypasses quiet hours
   and aggregation only if the server allows it, the aggregation policy allows it, and the user's
   threshold allows it. Mute is never bypassed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, tzinfo
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from src.core.notification_timing_schema import (
    DAY,
    DEFAULT_COALESCE_SECONDS,
    minutes,
    normalize_timing,
)
from src.features.notification_aggregation_policy import (
    IMMEDIATE,
    AggregationPolicy,
    policy_for,
)

Mode = Literal["inherit", "immediate", "coalesce", "digest", "mute"]
Action = Literal["deliver", "hold", "aggregate", "mute"]


def category_of(event_type: str, source_installation_id: object | None) -> str:
    """Coarse user-facing category; plugin producers are always ``plugin``."""
    if source_installation_id is not None:
        return "plugin"
    head = event_type.split(".", 1)[0]
    return head if head in ("media", "game", "system") else "plugin"


@dataclass(frozen=True)
class ServerTimingPolicy:
    """Administrator limits; users can narrow but never widen them."""

    enabled: bool = True
    urgent_bypass: bool = True


@dataclass(frozen=True)
class TimingFacts:  # pylint: disable=too-many-instance-attributes
    """The immutable facts a decision depends on (no ORM objects, so tests are trivial)."""

    event_type: str
    purpose: str
    category: str
    destination_id: str
    inbox: bool = False
    # What asked for urgency: the user's own route choice, the host severity, a plugin request.
    route_urgency: str = "normal"
    severity: str = "info"
    plugin_urgency: str = "normal"
    kind: str = ""
    group_key: str | None = None
    has_group_hint: bool = False


@dataclass(frozen=True)
class TimingDecision:
    """Explainable outcome; ``reasons`` is the ordered, human-readable derivation."""

    action: Action
    mode: str
    release_at: int | None = None
    aggregation: AggregationPolicy = IMMEDIATE
    bypassed: bool = False
    rule: str = "default"
    reasons: tuple[str, ...] = field(default_factory=tuple)

    @property
    def code(self) -> str:
        return self.reasons[-1].split(":", 1)[0] if self.reasons else self.action

    def explain(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "mode": self.mode,
            "release_at": self.release_at,
            "bypassed_by_urgency": self.bypassed,
            "matched_rule": self.rule,
            "reasons": list(self.reasons),
        }


def _tz(name: str) -> tzinfo:
    if not name:
        return UTC
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return UTC


def _local_wall(at: datetime, zone: tzinfo) -> tuple[Any, int]:
    local = at.astimezone(zone)
    return local.date(), local.hour * 60 + local.minute


def _instants(day: Any, minute: int, zone: tzinfo) -> list[int]:
    """UTC instants at which the wall clock of ``day`` reads ``minute`` (0, 1 or 2 of them).

    A skipped wall time (spring forward) resolves to the first instant after the gap; a repeated
    one (fall back) yields both occurrences, so callers can pick the first one after ``now``.
    """
    naive = datetime(day.year, day.month, day.day, minute // 60, minute % 60)
    found: list[int] = []
    for fold in (0, 1):
        stamp = int(naive.replace(tzinfo=zone, fold=fold).astimezone(UTC).timestamp())
        if stamp not in found:
            found.append(stamp)
    local = datetime.fromtimestamp(found[0], UTC).astimezone(zone)
    if (local.hour * 60 + local.minute) != minute:
        # Nonexistent local time: the two readings straddle the transition, which is where the
        # skipped wall time effectively "happens" (the first instant after the gap).
        return [(found[0] + found[-1]) // 2]
    return sorted(found)


def next_wall_time(now: int, minute: int, zone: tzinfo) -> int:
    """First instant strictly after ``now`` at which the local wall clock reads ``minute``."""
    start = datetime.fromtimestamp(now, UTC).astimezone(zone).date()
    for offset in range(0, 4):
        for stamp in _instants(start + timedelta(days=offset), minute, zone):
            if stamp > now:
                return stamp
    raise ValueError("No wall time found")  # pragma: no cover - unreachable for real zones


def in_quiet_hours(now: int, quiet: dict[str, Any], zone: tzinfo) -> bool:
    """Wall-clock membership of ``[start, end)``; an overnight range wraps midnight."""
    if not quiet.get("enabled"):
        return False
    start, end = minutes(quiet["start"]), minutes(quiet["end"])
    _, wall = _local_wall(datetime.fromtimestamp(now, UTC), zone)
    return start <= wall < end if start < end else wall >= start or wall < end


def quiet_release(now: int, quiet: dict[str, Any], zone: tzinfo) -> int | None:
    """When the quiet period containing ``now`` ends, or None if ``now`` is not quiet."""
    if not in_quiet_hours(now, quiet, zone):
        return None
    return next_wall_time(now, minutes(quiet["end"]), zone)


def next_digest(now: int, times: list[str], zone: tzinfo) -> int:
    return min(next_wall_time(now, minutes(value), zone) for value in times)


def _matching(rules: list[dict[str, Any]], facts: TimingFacts) -> list[tuple[str, dict[str, Any]]]:
    """Rules that apply, most specific first. Specificity is fixed, never list order."""
    ranked: list[tuple[int, str, dict[str, Any]]] = []
    for rule in rules:
        category, destination = rule["category"], rule["destination_id"]
        if category not in ("*", facts.category) or destination not in (None, facts.destination_id):
            continue
        rank = (2 if destination else 0) + (1 if category != "*" else 0)
        label = f"{category}+{'destination' if destination else 'any'}"
        ranked.append((rank, label, rule))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    return [(label, rule) for _, label, rule in ranked]


def _effective_rule(
    rules: list[tuple[str, dict[str, Any]]],
) -> tuple[str, str, dict[str, Any] | None, str, list[str]]:
    """Resolve each field from the most specific rule that sets it."""
    mode, label, urgent = "inherit", "default", "inherit"
    quiet: dict[str, Any] | None = None
    notes: list[str] = []
    for index, (name, rule) in enumerate(rules):
        if mode == "inherit" and rule["mode"] != "inherit":
            mode, label = rule["mode"], name
            notes.append(f"rule {name} sets mode {mode}")
        if quiet is None and rule.get("quiet_hours"):
            quiet = rule["quiet_hours"]
            notes.append(f"rule {name} sets quiet hours")
        if urgent == "inherit" and rule["urgent"] != "inherit":
            urgent = rule["urgent"]
            notes.append(f"rule {name} sets urgency {urgent}")
        if index == 0 and label == "default":
            label = name
    return mode, label, quiet, urgent, notes


def _urgent_requested(facts: TimingFacts, timing: dict[str, Any]) -> str | None:
    if facts.route_urgency == "critical":
        return "route"
    if facts.severity == "critical":
        return "severity"
    if facts.plugin_urgency == "critical" and timing["plugin_urgent_bypass"]:
        return "plugin"
    return None


# One linear derivation keeps the precedence readable in a single place.
# pylint: disable-next=too-many-locals,too-many-return-statements
def decide(
    facts: TimingFacts,
    timing: dict[str, Any] | None,
    *,
    now: int,
    server: ServerTimingPolicy | None = None,
) -> TimingDecision:
    """Resolve the effective, deterministic delivery timing for one delivery."""
    server = server or ServerTimingPolicy()
    base = policy_for(
        event_type=facts.event_type,
        kind=facts.kind,
        purpose=facts.purpose,
        group_key=facts.group_key,
        has_group_hint=facts.has_group_hint,
    )
    if facts.inbox or facts.purpose != "standard":
        why = "inbox" if facts.inbox else f"{facts.purpose} traffic"
        return TimingDecision("deliver", "immediate", reasons=(f"immune: {why} is never delayed",))
    timing = normalize_timing(timing or {})
    if not server.enabled:
        return TimingDecision(
            "aggregate" if base.strategy != "immediate" else "deliver",
            "inherit",
            aggregation=base,
            reasons=("server: user timing policies are disabled; event type defaults apply",),
        )
    zone = _tz(timing["timezone"])
    rules = _matching(timing["rules"], facts)
    mode, label, rule_quiet, urgent_rule, notes = _effective_rule(rules)
    reasons = list(notes) or ["no rule matched; account defaults apply"]
    if mode == "mute":
        return TimingDecision("mute", mode, rule=label, reasons=(*reasons, "mute: user muted it"))
    quiet = rule_quiet if rule_quiet is not None else timing["quiet_hours"]
    quiet = {**timing["quiet_hours"], **quiet}
    source = _urgent_requested(facts, timing)
    allowed = (
        source is not None
        and server.urgent_bypass
        and timing["urgent_threshold"] != "off"
        and urgent_rule != "deny"
    )
    if source is not None and not allowed:
        reasons.append(f"urgency: {source} request not honoured (server or user limit)")
    aggregation = base
    if mode == "immediate":
        aggregation = IMMEDIATE
    elif mode == "coalesce" and base.strategy != "coalesce":
        aggregation = AggregationPolicy("coalesce", DEFAULT_COALESCE_SECONDS, "event_type")
    elif mode == "digest":
        aggregation = AggregationPolicy("digest", DAY, "event_type", aligned=True)
    if allowed and aggregation.urgent_bypass:
        reasons.append(f"urgency: {source} request bypasses timing")
        return TimingDecision("deliver", mode, rule=label, bypassed=True, reasons=tuple(reasons))
    release: int | None = None
    if mode == "digest":
        release = next_digest(now, timing["digest_times"], zone)
        reasons.append("digest: next scheduled digest time")
    held = quiet_release(release if release is not None else now, quiet, zone)
    if held is not None:
        release = held
        reasons.append("quiet: delivery waits for the end of quiet hours")
    if release is not None:
        return TimingDecision(
            "hold", mode, release_at=release, aggregation=aggregation, rule=label,
            reasons=tuple(reasons),
        )  # fmt: skip
    if aggregation.strategy != "immediate":
        reasons.append(f"aggregate: {aggregation.strategy} window")
        return TimingDecision("aggregate", mode, aggregation=aggregation, rule=label,
                              reasons=tuple(reasons))  # fmt: skip
    return TimingDecision("deliver", mode, rule=label, reasons=(*reasons, "deliver: immediate"))
