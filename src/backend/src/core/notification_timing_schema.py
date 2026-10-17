"""Notification timing preference schema: defaults and strict validation (no heavy imports)."""

from __future__ import annotations

import re
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

MODES: tuple[str, ...] = ("inherit", "immediate", "coalesce", "digest", "mute")
CATEGORIES: tuple[str, ...] = ("*", "media", "game", "system", "plugin")
THRESHOLDS: tuple[str, ...] = ("critical", "off")
MAX_RULES = 64
MAX_DIGEST_TIMES = 6
DEFAULT_COALESCE_SECONDS = 900
DAY = 86400
_CLOCK = re.compile(r"([01]\d|2[0-3]):([0-5]\d)")

DEFAULT_TIMING: dict[str, Any] = {
    "timezone": "",
    "quiet_hours": {"enabled": False, "start": "22:00", "end": "07:00"},
    "urgent_threshold": "critical",
    "plugin_urgent_bypass": False,
    "digest_times": ["08:00"],
    "rules": [],
}


def valid_timezone(name: str) -> bool:
    if not name:
        return True
    if not re.fullmatch(r"[A-Za-z0-9_+\-/]{1,64}", name) or name.lower() == "localtime":
        return False
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return False
    return True


def minutes(clock: str) -> int:
    match = _CLOCK.fullmatch(clock)
    if match is None:
        raise ValueError("Times must be HH:MM")
    return int(match.group(1)) * 60 + int(match.group(2))


def normalize_timing(value: Any) -> dict[str, Any]:
    """Validate a stored/submitted document; unknown keys are rejected, defaults fill the rest."""
    if not isinstance(value, dict):
        raise ValueError("Notification timing must be an object")
    unknown = set(value) - set(DEFAULT_TIMING)
    if unknown:
        raise ValueError("Unknown notification timing fields")
    clean: dict[str, Any] = {**DEFAULT_TIMING, **value}
    zone = clean["timezone"]
    if not isinstance(zone, str) or not valid_timezone(zone):
        raise ValueError("timezone must be an IANA zone name or empty for UTC")
    clean["quiet_hours"] = _quiet(clean["quiet_hours"], partial=False)
    if clean["urgent_threshold"] not in THRESHOLDS:
        raise ValueError(f"urgent_threshold must be one of {list(THRESHOLDS)}")
    if not isinstance(clean["plugin_urgent_bypass"], bool):
        raise ValueError("plugin_urgent_bypass must be a boolean")
    times = clean["digest_times"]
    if (
        not isinstance(times, list)
        or not 1 <= len(times) <= MAX_DIGEST_TIMES
        or any(not isinstance(item, str) or not _CLOCK.fullmatch(item) for item in times)
    ):
        raise ValueError("digest_times must be 1 to 6 HH:MM values")
    clean["digest_times"] = sorted(set(times))
    clean["rules"] = _rules(clean["rules"])
    return clean


def _quiet(value: Any, *, partial: bool) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) - {"enabled", "start", "end"}:
        raise ValueError("quiet_hours takes enabled, start and end only")
    if partial and not value:
        return {}
    clean = {**DEFAULT_TIMING["quiet_hours"], **value} if not partial else dict(value)
    if "enabled" in clean and not isinstance(clean["enabled"], bool):
        raise ValueError("quiet_hours.enabled must be a boolean")
    for key in ("start", "end"):
        if key in clean and (not isinstance(clean[key], str) or not _CLOCK.fullmatch(clean[key])):
            raise ValueError("quiet hours use HH:MM")
    if not partial and clean["start"] == clean["end"]:
        raise ValueError("quiet hours need different start and end times")
    return clean


def _rules(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > MAX_RULES:
        raise ValueError(f"At most {MAX_RULES} timing rules are allowed")
    clean: list[dict[str, Any]] = []
    seen: set[tuple[str, str | None]] = set()
    for rule in value:
        if not isinstance(rule, dict) or set(rule) - {
            "category",
            "destination_id",
            "mode",
            "quiet_hours",
            "urgent",
        }:
            raise ValueError(
                "Timing rules take category, destination_id, mode, quiet_hours, urgent"
            )
        category = rule.get("category", "*")
        destination = rule.get("destination_id")
        mode = rule.get("mode", "inherit")
        if category not in CATEGORIES or mode not in MODES:
            raise ValueError("Unknown timing rule category or mode")
        if destination is not None:
            try:
                if not isinstance(destination, str) or str(UUID(destination)) != destination:
                    raise ValueError
            except (ValueError, AttributeError) as exc:
                raise ValueError("Timing rules require destination UUIDs") from exc
        urgent = rule.get("urgent", "inherit")
        if urgent not in ("inherit", "allow", "deny"):
            raise ValueError("urgent must be inherit, allow or deny")
        if (category, destination) in seen:
            raise ValueError("Each category and destination pair may have one timing rule")
        seen.add((category, destination))
        entry: dict[str, Any] = {
            "category": category,
            "destination_id": destination,
            "mode": mode,
            "urgent": urgent,
        }
        if "quiet_hours" in rule:
            entry["quiet_hours"] = _quiet(rule["quiet_hours"], partial=True)
        clean.append(entry)
    return clean
