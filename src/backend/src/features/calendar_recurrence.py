"""Windowed recurrence and reversible local overlays; upstream identity never moves."""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from dateutil.rrule import DAILY, MONTHLY, WEEKLY, YEARLY, rrule, rrulestr

from src.features.calendar_ics import (
    CalendarComponent,
    CalendarTime,
    valid_wall,
)

_FREQUENCIES = {"DAILY": DAILY, "WEEKLY": WEEKLY, "MONTHLY": MONTHLY, "YEARLY": YEARLY}
_ALLOWED = {"FREQ", "INTERVAL", "COUNT", "UNTIL", "BYDAY", "BYMONTHDAY"}


def rule_supported(component: CalendarComponent) -> bool:
    """A deliberately closed subset; unsupported rules retain one visible base occurrence."""
    # Independent rejection cases mirror the supported RFC property matrix.
    # pylint: disable=too-many-return-statements
    if not component.rrule or not component.start:
        return True
    try:
        parts = dict(pair.split("=", 1) for pair in component.rrule.split(";"))
        if set(parts) - _ALLOWED or parts.get("FREQ") not in _FREQUENCIES:
            return False
        if "COUNT" in parts and "UNTIL" in parts:
            return False
        if not 1 <= int(parts.get("INTERVAL", "1")) <= 366:
            return False
        if "COUNT" in parts and not 1 <= int(parts["COUNT"]) <= 10_000:
            return False
        if "BYDAY" in parts:
            days = parts["BYDAY"].split(",")
            if len(days) > 35 or any(
                not re.fullmatch(r"[+-]?[1-5]?(MO|TU|WE|TH|FR|SA|SU)", day) for day in days
            ):
                return False
            if parts["FREQ"] in {"DAILY", "WEEKLY"} and any(len(day) != 2 for day in days):
                return False
        if "BYMONTHDAY" in parts:
            values = parts["BYMONTHDAY"].split(",")
            if len(values) > 62 or any(
                not -31 <= int(value) <= 31 or int(value) == 0 for value in values
            ):
                return False
        rrulestr(component.rrule, dtstart=component.start.datetime())
        return True
    except (ValueError, TypeError, OverflowError):
        return False


def _time(value: datetime, template: CalendarTime) -> CalendarTime:
    if template.kind == "date":
        return CalendarTime(kind="date", value=value.date().isoformat())
    if template.kind == "zoned":
        return CalendarTime(
            kind="zoned", value=value.replace(tzinfo=None).isoformat(), tzid=template.tzid
        )
    return CalendarTime(kind=template.kind, value=value.isoformat())


def _starts(
    component: CalendarComponent,
    lower: datetime,
    upper: datetime,
    warnings: set[str],
    budget: list[int],
) -> list[CalendarTime]:
    # Keep recurrence-domain conversion, COUNT and DST validation together.
    # pylint: disable=too-many-locals,too-many-branches
    if not component.start:
        return []
    template = component.start
    original = template.datetime()
    result = {template.key(): template}
    if component.rrule and rule_supported(component):
        rule = rrulestr(component.rrule, dtstart=original)
        if not isinstance(rule, rrule):
            warnings.add("unsupported_recurrence")
            return list(result.values())
        # Match the rule's naive/aware domain; the query includes a one-day timezone margin.
        lo = lower.replace(tzinfo=original.tzinfo)
        hi = upper.replace(tzinfo=original.tzinfo)
        parts = dict(pair.split("=", 1) for pair in component.rrule.split(";"))
        count = int(parts["COUNT"]) if "COUNT" in parts else None
        # Bound even impossible rules (e.g. February 30) by the visible window's end.
        until_text = parts.get("UNTIL")
        until = None
        if until_text:
            until = (
                datetime.strptime(until_text, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
                if until_text.endswith("Z")
                else datetime.strptime(
                    until_text, "%Y%m%d" if len(until_text) == 8 else "%Y%m%dT%H%M%S"
                )
            )
        if until is not None:
            hi = min(hi, until)
        if hi >= original:
            if count is None and parts["FREQ"] in {"DAILY", "WEEKLY"}:
                step = int(parts.get("INTERVAL", "1")) * (7 if parts["FREQ"] == "WEEKLY" else 1)
                periods = max(0, (lo.date() - original.date()).days // step - 1)
                rule = rule.replace(dtstart=original + timedelta(days=periods * step))
            rule = rule.replace(until=hi, count=None)
            seen = 0
            for value in rule:
                budget[0] -= 1
                if budget[0] < 0:
                    warnings.add("expansion_limit")
                    break
                if not valid_wall(value):
                    continue
                seen += 1
                if count is not None and seen > count:
                    break
                if value >= lo:
                    instant = _time(value, template)
                    result[instant.key()] = instant
                    if len(result) >= 366:
                        warnings.add("expansion_limit")
                        break
    elif component.rrule:
        warnings.add("unsupported_recurrence")
    for extra in component.rdates:
        result[extra.key()] = extra
    for excluded in component.exdates:
        result.pop(excluded.key(), None)
    return list(result.values())


def _display(value: CalendarTime, zone: ZoneInfo) -> datetime:
    stamp = value.datetime()
    return stamp.astimezone(zone) if stamp.tzinfo else stamp


def expand_calendar(
    components: list[tuple[str, CalendarComponent]],
    overlays: dict[tuple[str, str], dict[str, Any]],
    start: date,
    end: date,
    timezone: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Return inclusive visible dates, keeping recurrence keys at their original upstream times."""
    # Master, exception, exclusion and two overlay scopes must resolve in this explicit order.
    # pylint: disable=too-many-locals,too-many-branches,too-many-statements
    zone = ZoneInfo(timezone)
    warnings: set[str] = set()
    result: list[dict[str, Any]] = []
    grouped: dict[str, dict[str, tuple[str, CalendarComponent]]] = {}
    for row_id, component in components:
        grouped.setdefault(component.uid, {})[component.recurrence_id] = (row_id, component)
    budget = [20_000]
    for uid, members in grouped.items():
        base = members.get("")
        series = overlays.get((uid, "*"), {})
        # Include moved occurrences whose original position lies outside the window.
        shift = timedelta(seconds=int(series.get("shift_seconds", 0)))
        duration = int(series.get("duration_seconds", base[1].duration_seconds if base else 0))
        lower = (
            datetime.combine(start, datetime.min.time())
            - shift
            - timedelta(seconds=duration)
            - timedelta(days=1)
        )
        upper = datetime.combine(end, datetime.max.time()) - shift + timedelta(days=1)
        candidates: dict[str, tuple[str, CalendarComponent, CalendarTime]] = {}
        if base and not base[1].cancelled and base[1].start:
            for value in _starts(base[1], lower, upper, warnings, budget):
                key = value.key() if base[1].rrule or base[1].rdates else ""
                candidates[key] = (base[0], base[1], value)
        for key, (row_id, component) in members.items():
            if key:
                candidates.pop(key, None)
                if base and any(excluded.key() == key for excluded in base[1].exdates):
                    continue
                if component.start and not component.cancelled and not (base and base[1].cancelled):
                    candidates[key] = (row_id, component, component.start)
        # Local moved exceptions outside the expansion range still need a candidate.
        if base and base[1].start and not base[1].cancelled:
            for (overlay_uid, key), fields in overlays.items():
                if (
                    overlay_uid == uid
                    and key not in {"*", ""}
                    and key not in candidates
                    and key not in members
                    and "start" in fields
                ):
                    if any(value.key() == key for value in base[1].exdates):
                        continue
                    if key.startswith("D:"):
                        value = CalendarTime(kind="date", value=key[2:])
                    elif key.startswith("F:"):
                        value = CalendarTime(kind="floating", value=key[2:])
                    else:
                        value = CalendarTime(kind="utc", value=key[2:])
                    stamp = value.datetime().replace(tzinfo=None)
                    actual = _starts(
                        base[1],
                        stamp - timedelta(days=1),
                        stamp + timedelta(days=1),
                        warnings,
                        budget,
                    )
                    if any(item.key() == key for item in actual):
                        candidates[key] = (base[0], base[1], value)
        for key, (row_id, component, original_start) in candidates.items():
            if component.cancelled:
                continue
            value = original_start
            if shift:
                value = _time(value.datetime() + shift, value)
            fields = {**series, **overlays.get((uid, key), {})}
            if "start" in fields:
                value = CalendarTime.model_validate(fields["start"])
            stamp = _display(value, zone)
            duration = int(fields.get("duration_seconds", component.duration_seconds))
            finish = (
                (stamp.astimezone(UTC) + timedelta(seconds=duration)).astimezone(zone)
                if stamp.tzinfo
                else stamp + timedelta(seconds=duration)
            )
            last_day = (finish - timedelta(microseconds=1)).date() if duration else stamp.date()
            if stamp.date() > end or last_day < start or not valid_wall(value.datetime()):
                continue
            result.append(
                {
                    "event_id": row_id,
                    "uid": uid,
                    "recurrence_key": key,
                    "title": fields.get("title", component.title),
                    "note": fields.get("note", component.note),
                    "location": fields.get("location", component.location),
                    "url": component.url,
                    "start": value.model_dump(),
                    "original_start": original_start.model_dump(),
                    "duration_seconds": duration,
                    "event_date": stamp.date().isoformat(),
                    "event_time": None if value.kind == "date" else stamp.strftime("%H:%M"),
                    "end_date": finish.date().isoformat(),
                    "end_time": None if value.kind == "date" else finish.strftime("%H:%M"),
                    "last_date": last_day.isoformat(),
                    "recurring": bool(key),
                    "series_override": bool(series),
                    "occurrence_override": (uid, key) in overlays,
                }
            )
            if len(result) >= 1000:
                warnings.add("expansion_limit")
                return result, sorted(warnings)
    return result, sorted(warnings)
