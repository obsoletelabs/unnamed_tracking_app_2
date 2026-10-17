"""Dry-run notification diagnostics: reason traces, render and digest previews, provider health.

Everything in this module only reads. Simulations evaluate unsaved, synthetic events against the
caller's own destinations and preferences with the production policy functions (never copies of
them), and previews render host-authored fixture content with the production renderer. Callers
roll the session back afterwards as a second guard, and tests assert that no notification,
delivery, receipt, window or story row is ever created. Free text that originates outside the host
(destination names, plugin names) is redacted; fixture content is host-authored and shown as is.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import settings
from src.core.notification_timing_schema import normalize_timing
from src.core.preferences import load_preferences
from src.database.models.notification import Notification
from src.database.models.notification_delivery import NotificationDelivery
from src.database.models.notification_delivery_attempt import NotificationDeliveryAttempt
from src.database.models.notification_destination import NotificationDestination
from src.database.models.notification_provider_setting import NotificationProviderSetting
from src.database.models.plugin_notification_provider import PluginNotificationProviderRegistration
from src.features import notification_aggregation_policy as grouping
from src.features.notification_audit import change_deliveries
from src.features.notification_contract import (
    BUILTIN_PROFILES,
    RevisionDecision,
    StoryHead,
    decide_revision,
)
from src.features.notification_diagnostic_fixtures import (
    FAILURES,
    FIXTURES,
    SENSITIVE_PURPOSES,
    reason,
    redact,
)
from src.features.notification_plugin_policy import delivery_projection
from src.features.notification_policy import (
    INBOX_PROVIDER,
    effective_trust,
    preference_enabled,
    route_choice,
)
from src.features.notification_providers.base import DigestItem, NotificationMessage
from src.features.notification_providers.capabilities import ResolvedProfile, resolve_profile
from src.features.notification_providers.delivery import (
    LEASE_SECONDS,
    MAX_ATTEMPTS,
    TRANSPORT_TIMEOUT_SECONDS,
    retry_delay,
)
from src.features.notification_providers.registry import get_notification_providers
from src.features.notification_push_config import push_configuration
from src.features.notification_render import render_message
from src.features.notification_stories import PLUGIN_UPDATE_LIFECYCLE
from src.features.notification_timing import decide_for
from src.features.smtp_configuration import smtp_configuration

MAX_DESTINATIONS = 10
MAX_DIGEST_ITEMS = 200
PREVIEW_BASE = "https://tracker.example"
HEALTH_WINDOW_SECONDS = 86400


def _stage(stage: str, code: str, **detail: Any) -> dict[str, Any]:
    return {"stage": stage, **reason(code), "detail": detail}


def fixture_catalog() -> list[dict[str, Any]]:
    return [
        {
            "id": key,
            "label": spec["label"],
            "description": spec["description"],
            "sendable": spec.get("sendable", True),
            "sensitive": spec.get("purpose") in SENSITIVE_PURPOSES,
        }
        for key, spec in FIXTURES.items()
    ]


def _clock(timestamp: int) -> str:
    return datetime.fromtimestamp(timestamp, UTC).strftime("%H:%M")


def _probe(spec: dict[str, Any], now: int, destination: NotificationDestination) -> Notification:
    """An unsaved event; verification-style probes are bound to the destination under test."""
    purpose = spec.get("purpose", "standard")
    sensitive = purpose in SENSITIVE_PURPOSES
    return Notification(
        user_id=destination.user_id,
        event_type=spec["event_type"],
        kind=spec["event_type"].rsplit(".", 1)[-1],
        purpose=purpose,
        severity=spec.get("severity", "info"),
        requested_urgency=spec.get("urgency", "normal"),
        required_trust=2 if sensitive else 1,
        event_at=now,
        media_type="notification_destination" if purpose == "verification" else "diagnostics",
        media_id=destination.id if purpose == "verification" else uuid4(),
        source="host.diagnostics",
        group_key=None,
    )


def ingestion_trace(spec: dict[str, Any], now: int) -> list[dict[str, Any]]:
    """Receipt deduplication and story transitions, using the production decision function."""
    steps: list[dict[str, Any]] = []
    for number in range(1, int(spec.get("duplicates", 1)) + 1):
        code = "accepted" if number == 1 else "deduplicated"
        steps.append(_stage("ingestion", code, delivery_of_event=number))
    phases = spec.get("phases")
    if not phases:
        return steps
    head: StoryHead | None = None
    item = SimpleNamespace(
        deleted_at=None, dismissed_at=None, read_at=None, event_at=now, expires_at=None
    )
    for phase in phases:
        decision = decide_revision(
            PLUGIN_UPDATE_LIFECYCLE, head, phase, now=now, retention_cutoff=None
        )
        steps.append(_stage("ingestion", decision.value, phase=phase))
        if decision in {RevisionDecision.CREATE_STORY, RevisionDecision.UPDATE_IN_PLACE}:
            head = StoryHead(phase, (head.revision if head else 0) + 1, item)
    return steps


def grouping_trace(probe: Notification, now: int) -> dict[str, Any]:
    policy = grouping.policy_for(
        event_type=probe.event_type,
        kind=probe.kind,
        purpose=probe.purpose,
        group_key=None,
        has_group_hint=False,
    )
    bypass = grouping.bypasses(
        policy, requested_urgency=probe.requested_urgency, severity=probe.severity
    )
    start, end = grouping.window_bounds(policy, now)
    code = "urgent_bypass" if bypass and policy.strategy != "immediate" else policy.strategy
    return _stage(
        "grouping",
        code,
        strategy=policy.strategy,
        scope=policy.scope,
        window_seconds=policy.window_seconds,
        window_start=None if policy.strategy == "immediate" else start,
        window_end=None if policy.strategy == "immediate" else end,
    )


def eligibility_code(
    probe: Notification,
    destination: NotificationDestination,
    preferences: dict[str, Any],
    *,
    provider_enabled: bool,
    projection: str | None,
) -> str:
    """The first reason routing denies work, mirroring the order of the production checks."""
    inbox = destination.provider_id == INBOX_PROVIDER
    blocked = {item.strip() for item in settings.NOTIFICATION_BLOCKED_PROVIDERS.split(",")}
    blocked_types = {item.strip() for item in settings.NOTIFICATION_BLOCKED_TYPES.split(",")}
    checks = (
        ("provider_disabled", not inbox and not provider_enabled),
        ("destination_inactive", not destination.active or not destination.enabled),
        (
            "destination_inactive",
            preferences.get("notification_destinations", {}).get(str(destination.id)) is False,
        ),
        ("preference_disabled", not preference_enabled(probe, preferences)),
        (
            "test_bound_to_destination",
            probe.purpose in {"verification", "test"} and probe.media_id != destination.id,
        ),
        (
            "route_disabled",
            not route_choice(probe.event_type, str(destination.id), preferences)["enabled"],
        ),
        (
            "blocked_by_admin",
            destination.provider_id in blocked or probe.event_type in blocked_types,
        ),
    )
    for code, denied in checks:
        if denied:
            return code
    if projection is None:
        return "sensitive_not_permitted" if probe.purpose in SENSITIVE_PURPOSES else "trust_too_low"
    return "eligible"


def failure_attempts(failure: str, *, start: int) -> dict[str, Any]:
    """The dispatcher's retry state machine for a simulated provider outcome (no provider call)."""
    attempts: list[dict[str, Any]] = []
    if failure == "none":
        return {
            "final_status": "sent",
            "attempts": [{"number": 1, "outcome": "sent", **reason("sent")}],
        }
    if failure == "unavailable":
        # The dispatcher defers without consuming an attempt while the provider is unreachable.
        return {
            "final_status": "retry_wait",
            "attempts": [
                {
                    "number": 0,
                    "outcome": "deferred",
                    **reason("provider_unavailable"),
                    "next_attempt_at": start + 60,
                }
            ],
        }
    code = {
        "transient": "provider_error",
        "rate_limited": "rate_limited",
        "invalid_config": "invalid_config",
        "permanent": "failed_permanent",
    }[failure]
    retryable = failure in {"transient", "rate_limited"}
    for number in range(1, MAX_ATTEMPTS + 1):
        if failure == "transient" and number == MAX_ATTEMPTS:
            attempts.append({"number": number, "outcome": "sent", **reason("sent")})
            return {"final_status": "sent", "attempts": attempts}
        entry: dict[str, Any] = {"number": number, "outcome": "failed", **reason(code)}
        if retryable and number < MAX_ATTEMPTS:
            entry["next_attempt_at"] = start + retry_delay(number)
            start = entry["next_attempt_at"]
        attempts.append(entry)
        if not retryable:
            return {"final_status": "failed_permanent", "attempts": attempts}
    attempts[-1].update(reason("attempts_exhausted"), outcome="failed")
    return {"final_status": "failed_permanent", "attempts": attempts}


def profile_summary(profile: ResolvedProfile | None) -> dict[str, Any] | None:
    if profile is None:
        return None
    return {
        "renderer": profile.renderer,
        "critical_supported": profile.critical_supported,
        "notes": [reason(note)["code"] for note in profile.notes],
        "capabilities": profile.capabilities.model_dump(mode="json"),
    }


async def _providers(db: AsyncSession) -> dict[str, Any]:
    providers = dict(await get_notification_providers(db))
    providers.setdefault(INBOX_PROVIDER, SimpleNamespace(id=INBOX_PROVIDER, name="Inbox"))
    return providers


def _profile(providers: dict[str, Any], provider_id: str) -> ResolvedProfile | None:
    provider = providers.get(provider_id)
    return resolve_profile(provider) if provider is not None else None


async def own_destinations(
    db: AsyncSession, user_id: UUID, destination_id: UUID | None = None
) -> list[NotificationDestination]:
    """Read-only (unlike dispatch enrolment): only the caller's own, bounded."""
    query = select(NotificationDestination).where(NotificationDestination.user_id == user_id)
    if destination_id is not None:
        query = query.where(NotificationDestination.id == destination_id)
    rows = await db.scalars(query.order_by(NotificationDestination.id).limit(MAX_DESTINATIONS))
    return list(rows)


def _apply_overrides(spec: dict[str, Any], preferences: dict[str, Any], now: int) -> dict[str, Any]:
    prefs = dict(preferences)
    if spec.get("quiet_hours"):
        window = {
            "enabled": True,
            "start": _clock(now - 3600),
            "end": _clock(now + 3600),
        }
        prefs["notification_timing"] = normalize_timing(
            {**(prefs.get("notification_timing") or {}), "quiet_hours": window}
        )
    return prefs


async def _destination_trace(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    db: AsyncSession,
    destination: NotificationDestination,
    spec: dict[str, Any],
    prefs: dict[str, Any],
    enabled: set[str],
    providers: dict[str, Any],
    now: int,
) -> dict[str, Any]:
    probe = _probe(spec, now, destination)
    projection = await delivery_projection(db, probe, destination, prefs)
    code = eligibility_code(
        probe,
        destination,
        prefs,
        provider_enabled=destination.provider_id in enabled,
        projection=projection,
    )
    profile = _profile(providers, destination.provider_id)
    stages = [
        _stage(
            "eligibility",
            code,
            projection=projection,
            trust=effective_trust(destination).name,
            required_trust=max(probe.required_trust, settings.NOTIFICATION_MINIMUM_TRUST),
        )
    ]
    if code == "eligible":
        decision = decide_for(probe, destination, prefs, now=now)
        timing_code = (
            "muted"
            if decision.action == "mute"
            else "immediate"
            if decision.action == "deliver" and not decision.bypassed
            else "urgent_bypass"
            if decision.bypassed
            else "quiet_hours"
            if decision.action == "hold"
            else decision.aggregation.strategy
        )
        stages.insert(0, _stage("timing", timing_code, **_timing_detail(decision)))
        stages.append(_capability_stage(profile))
        stages.append(_delivery_stage(spec, decision, profile, now))
    else:
        stages.append(_stage("delivery", code, status="not_queued"))
    return {
        "destination_id": str(destination.id),
        "provider_id": destination.provider_id,
        "name": redact(destination.display_name or destination.kind, 80),
        "stages": stages,
    }


def _timing_detail(decision: Any) -> dict[str, Any]:
    explained = decision.explain()
    return {
        "action": explained["action"],
        "mode": explained["mode"],
        "release_at": explained["release_at"],
        "bypassed_by_urgency": explained["bypassed_by_urgency"],
        "matched_rule": explained["matched_rule"],
        "derivation": [redact(item, 200) for item in explained["reasons"]],
    }


def _capability_stage(profile: ResolvedProfile | None) -> dict[str, Any]:
    if profile is None:
        return _stage("capability", "provider_unavailable")
    return _stage("capability", "eligible", **(profile_summary(profile) or {}))


def _delivery_stage(
    spec: dict[str, Any], decision: Any, profile: ResolvedProfile | None, now: int
) -> dict[str, Any]:
    if profile is None:
        return _stage("delivery", "provider_unavailable", status="retry_wait")
    if decision.action == "mute":
        return _stage("delivery", "muted", status="not_queued")
    if decision.action != "deliver":
        held = "quiet_hours" if decision.action == "hold" else decision.aggregation.strategy
        return _stage("delivery", held, status="held", release_at=decision.release_at)
    outcome = failure_attempts(spec.get("failure", "none"), start=now)
    return _stage(
        "delivery", outcome["attempts"][-1]["code"], status=outcome["final_status"], **outcome
    )


async def _enabled_providers(db: AsyncSession, user_id: UUID) -> set[str]:
    return set(
        await db.scalars(
            select(NotificationProviderSetting.provider_id).where(
                NotificationProviderSetting.user_id == user_id,
                NotificationProviderSetting.enabled.is_(True),
            )
        )
    )


async def simulate(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    db: AsyncSession,
    user_id: UUID,
    fixture_id: str,
    *,
    destination_id: UUID | None = None,
    at: int | None = None,
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Trace one synthetic event through ingestion, grouping, timing, eligibility and delivery."""
    base = FIXTURES.get(fixture_id)
    if base is None:
        raise KeyError(fixture_id)
    spec = {**base, **{k: v for k, v in (overrides or {}).items() if v is not None}}
    if spec.get("failure", "none") not in FAILURES:
        raise ValueError("Unknown failure mode")
    now = int(time.time()) if at is None else at
    prefs = _apply_overrides(spec, await load_preferences(db, user_id), now)
    enabled = await _enabled_providers(db, user_id)
    destinations = await own_destinations(db, user_id, destination_id)
    probe = _probe(spec, now, destinations[0]) if destinations else None
    shared: list[dict[str, Any]] = ingestion_trace(spec, now)
    if probe is not None:
        shared.append(grouping_trace(probe, now))
    traces = [
        await _destination_trace(db, destination, spec, prefs, enabled, await _providers(db), now)
        for destination in destinations
    ]
    if not traces:
        shared.append(_stage("eligibility", "no_destination"))
    return {
        "dry_run": True,
        "fixture": fixture_id,
        "evaluated_at": now,
        "event_type": spec["event_type"],
        "purpose": spec.get("purpose", "standard"),
        "stages": shared,
        "destinations": traces,
    }


async def render_previews(
    db: AsyncSession, variant: str, provider_id: str | None = None
) -> list[dict[str, Any]]:
    """What each provider would receive for fixture content; renders without queueing anything."""
    providers = await _providers(db)
    selected = [provider_id] if provider_id else sorted(providers)
    now = int(time.time())
    results: list[dict[str, Any]] = []
    for identifier in selected:
        profile = _profile(providers, identifier)
        if profile is None:
            results.append({"provider_id": identifier, **reason("provider_unavailable")})
            continue
        notice = Notification(
            id=uuid4(),
            kind="provider_test",
            event_type="notification.provider.test",
            purpose="test",
            group_key=f"variant:{variant}",
            media_type="diagnostics",
            media_id=uuid4(),
            event_at=now,
            title="Test notification",
            body="Example release: A new episode is available. This is a delivery test.",
            severity="info",
            source="host.test",
        )
        message = NotificationMessage(
            notice.id, notice.kind, notice.title, notice.body, "diagnostics", notice.media_id, now
        )
        rendering = await render_message(
            db,
            message,
            notice,
            projection="canonical",
            profile=profile,
            operation="send",
            remote_ref=None,
            link_base=PREVIEW_BASE,
            revision=0,
            idempotency_key="preview",
        )
        rendered = rendering.message
        caps = profile.capabilities
        results.append(
            {
                "provider_id": identifier,
                "renderer": profile.renderer,
                "title": rendered.title,
                "body": rendered.body,
                "html": (rendered.html or "")[:20000] or None,
                "actions": [{"label": a.label, "url": a.url} for a in rendered.actions],
                "images": list(rendered.images),
                "link": rendered.link,
                "fallbacks": [reason(code) for code in rendering.fallbacks],
                "content_hash": rendering.content_hash,
                "limits": {
                    "title_chars": caps.max_title_chars,
                    "text_chars": caps.max_text_chars,
                    "payload_bytes": caps.max_payload_bytes,
                    "action_buttons": caps.action_buttons,
                    "images": caps.images.max_images if caps.images else 0,
                },
            }
        )
    return results


def _digest_chunks(
    event_type: str, count: int, limits: grouping.ProviderLimits, now: int
) -> list[dict[str, Any]]:
    items = [
        DigestItem(
            uuid4(),
            "digest_example",
            event_type,
            "diagnostics",
            uuid4(),
            now - number * 60,
            f"Example item {number + 1}",
            f"Example item {number + 1} is available.",
        )
        for number in range(count)
    ]
    chunks = grouping.chunk_items(items, limits)
    composed = []
    for index, chunk in enumerate(chunks, 1):
        title, body = grouping.compose_digest(chunk, limits=limits, part=index, parts=len(chunks))
        composed.append({"part": index, "items": len(chunk), "title": title, "body": body})
    return composed


async def digest_preview(
    db: AsyncSession, event_type: str, count: int, provider_id: str, at: int | None = None
) -> dict[str, Any]:
    """How a run of events would be grouped and chunked for one provider (a pure dry run)."""
    now = int(time.time()) if at is None else at
    policy = grouping.policy_for(
        event_type=event_type,
        kind=event_type.rsplit(".", 1)[-1],
        purpose="standard",
        group_key=None,
        has_group_hint=False,
    )
    providers = await _providers(db)
    profile = _profile(providers, provider_id)
    if profile is None:
        return {"dry_run": True, **reason("provider_unavailable"), "chunks": []}
    start, end = grouping.window_bounds(policy, now)
    result: dict[str, Any] = {
        "dry_run": True,
        "event_type": event_type,
        "strategy": policy.strategy,
        "scope": policy.scope,
        "window_seconds": policy.window_seconds,
        "window_start": start if policy.strategy != "immediate" else None,
        "window_end": end if policy.strategy != "immediate" else None,
        "items": count,
        "chunks": [],
        "reason": reason("immediate" if policy.strategy == "immediate" else policy.strategy),
    }
    if policy.strategy == "immediate":
        return result
    limits = grouping.provider_limits(provider_id, None, profile=profile)
    result["chunks"] = _digest_chunks(event_type, min(count, MAX_DIGEST_ITEMS), limits, now)
    return result


def _provider_health(
    provider_id: str, registration: Any, smtp_ok: bool, push_ok: bool
) -> tuple[str, dict[str, Any]]:
    """A configuration check only; no network probe, no secret material."""
    if provider_id == "core.smtp":
        return ("ok" if smtp_ok else "not_configured"), {"smtp_configured": smtp_ok}
    if provider_id == "core.browser":
        return ("ok" if push_ok else "not_configured"), {"push_configured": push_ok}
    if provider_id == INBOX_PROVIDER:
        return "ok", {}
    if registration is None:
        return "provider_unavailable", {}
    needs_server = bool(registration.definition and registration.definition.get("server_fields"))
    if needs_server and not registration.server_configured:
        return "invalid_config", {"server_configured": False}
    return "ok", {"server_configured": bool(registration.server_configured)}


async def provider_overview(db: AsyncSession) -> list[dict[str, Any]]:
    """Capability declarations, configuration health and server-wide outcome counts."""
    providers = await _providers(db)
    registrations = {
        row.provider_id: row
        for row in await db.scalars(
            select(PluginNotificationProviderRegistration).where(
                PluginNotificationProviderRegistration.revoked_at.is_(None)
            )
        )
    }
    smtp_ok = (await smtp_configuration(db)).configured
    push_ok = (await push_configuration(db)).configured
    since = int(time.time()) - HEALTH_WINDOW_SECONDS
    counts = (
        await db.execute(
            select(NotificationDelivery.provider_id, NotificationDelivery.status, func.count())  # pylint: disable=not-callable
            .where(NotificationDelivery.attempted_at >= since)
            .group_by(NotificationDelivery.provider_id, NotificationDelivery.status)
        )
    ).all()
    errors = (
        await db.execute(
            select(NotificationDelivery.provider_id, NotificationDelivery.last_error, func.count())  # pylint: disable=not-callable
            .where(
                NotificationDelivery.attempted_at >= since,
                NotificationDelivery.last_error.isnot(None),
            )
            .group_by(NotificationDelivery.provider_id, NotificationDelivery.last_error)
        )
    ).all()
    result = []
    for identifier in sorted(providers):
        status, health = _provider_health(
            identifier, registrations.get(identifier), smtp_ok, push_ok
        )
        profile = _profile(providers, identifier)
        result.append(
            {
                "provider_id": identifier,
                "name": redact(str(getattr(providers[identifier], "name", identifier)), 80),
                "kind": "plugin" if identifier in registrations else "built_in",
                "built_in_profile": identifier in BUILTIN_PROFILES,
                "health": {**reason(status), "status": status, **health, "probe": "configuration"},
                "profile": profile_summary(profile),
                "last_24h": {
                    "statuses": {s: n for p, s, n in counts if p == identifier},
                    "error_codes": {reason(e)["code"]: n for p, e, n in errors if p == identifier},
                },
            }
        )
    return result


def retry_policy() -> dict[str, Any]:
    return {
        "max_attempts": MAX_ATTEMPTS,
        "backoff_seconds": [retry_delay(n) for n in range(1, MAX_ATTEMPTS)],
        "lease_seconds": LEASE_SECONDS,
        "transport_timeout_seconds": TRANSPORT_TIMEOUT_SECONDS,
        "delivery_semantics": "at_least_once",
    }


async def user_deliveries(
    db: AsyncSession, user_id: UUID, *, limit: int = 25, status: str | None = None
) -> list[dict[str, Any]]:
    """The caller's own deliveries and attempts: codes and next actions, never content."""
    now = int(time.time())
    query = (
        select(NotificationDelivery, Notification, NotificationDestination.display_name)
        .join(Notification, Notification.id == NotificationDelivery.notification_id)
        .outerjoin(
            NotificationDestination,
            NotificationDestination.id == NotificationDelivery.destination_id,
        )
        .where(Notification.user_id == user_id, NotificationDelivery.provider_id != INBOX_PROVIDER)
        .order_by(Notification.event_at.desc(), NotificationDelivery.id)
        .limit(limit)
    )
    if status:
        query = query.where(NotificationDelivery.status == status)
    rows = (await db.execute(query)).all()
    ids = [delivery.id for delivery, _, _ in rows]
    attempts: dict[UUID, list[NotificationDeliveryAttempt]] = {}
    if ids:
        for attempt in await db.scalars(
            select(NotificationDeliveryAttempt)
            .where(NotificationDeliveryAttempt.delivery_id.in_(ids))
            .order_by(NotificationDeliveryAttempt.delivery_id, NotificationDeliveryAttempt.number)
        ):
            attempts.setdefault(attempt.delivery_id, []).append(attempt)
    return [
        {
            "id": str(delivery.id),
            "event_type": redact(notice.event_type, 120),
            "purpose": notice.purpose,
            "test": notice.purpose == "test",
            "provider_id": delivery.provider_id,
            "destination": redact(name or "", 80) or None,
            "status": delivery.status,
            "operation": delivery.operation,
            "attempts": delivery.attempts,
            "max_attempts": MAX_ATTEMPTS,
            "reason": reason(delivery.last_error) if delivery.last_error else None,
            "fallbacks": [reason(c) for c in (delivery.fallback or "").split(",") if c],
            "next_attempt_at": delivery.next_attempt_at
            if delivery.status == "retry_wait"
            else None,
            "retry_allowed": retry_allowed(delivery, notice, now),
            "attempt_log": [
                {
                    "number": a.number,
                    "outcome": a.outcome,
                    "operation": a.operation,
                    "started_at": a.started_at,
                    "finished_at": a.finished_at,
                    "reason": reason(a.error_code) if a.error_code else None,
                }
                for a in attempts.get(delivery.id, [])
            ],
        }
        for delivery, notice, name in rows
    ]


RETRY_COOLDOWN_SECONDS = 60
RETRYABLE_PURPOSES = frozenset({"test", "standard"})


def retry_allowed(delivery: NotificationDelivery, notice: Notification, now: int) -> bool:
    """Owners may re-queue failed ordinary work within its attempt budget; never sensitive flows."""
    return (
        delivery.status in {"failed_permanent", "retry_wait"}
        and notice.purpose in RETRYABLE_PURPOSES
        and delivery.attempts < MAX_ATTEMPTS
        and notice.deleted_at is None
        and (notice.expires_at is None or notice.expires_at > now)
        and (delivery.attempted_at or 0) + RETRY_COOLDOWN_SECONDS <= now
    )


class RetryRefused(Exception):
    """The delivery cannot be re-queued; ``status_code`` follows HTTP semantics."""

    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


async def retry_delivery(db: AsyncSession, user_id: UUID, delivery_id: UUID) -> dict[str, Any]:
    """Owner-requested retry through the real dispatcher; authorization is rechecked per send."""
    row = (
        await db.execute(
            select(NotificationDelivery, Notification)
            .join(Notification, Notification.id == NotificationDelivery.notification_id)
            .where(NotificationDelivery.id == delivery_id, Notification.user_id == user_id)
            .with_for_update(of=NotificationDelivery)
        )
    ).first()
    if row is None:
        raise RetryRefused("Delivery not found", 404)
    delivery, notice = row
    now = int(time.time())
    if notice.purpose not in RETRYABLE_PURPOSES:
        raise RetryRefused("Sensitive notifications are never re-sent from diagnostics", 409)
    if delivery.attempts >= MAX_ATTEMPTS:
        raise RetryRefused("All attempts were used; send a new test instead", 409)
    if not retry_allowed(delivery, notice, now):
        recent = (delivery.attempted_at or 0) + RETRY_COOLDOWN_SECONDS > now
        raise RetryRefused(
            "Wait a minute before retrying" if recent else "This delivery cannot be retried",
            429 if recent else 409,
        )
    await change_deliveries(
        db,
        update(NotificationDelivery)
        .where(
            NotificationDelivery.id == delivery.id,
            NotificationDelivery.status.in_(("failed_permanent", "retry_wait")),
        )
        .values(status="pending", last_error=None, next_attempt_at=now),
    )
    await db.commit()
    return {"id": str(delivery_id), "status": "pending"}
