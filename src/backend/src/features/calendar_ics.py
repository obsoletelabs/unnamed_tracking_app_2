"""Bounded iCalendar parsing into an owner-local, versioned snapshot (no network)."""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from icalendar import Calendar
from pydantic import BaseModel, Field, model_validator

MAX_BYTES = 2 * 1024 * 1024
MAX_COMPONENTS = 10_000
MAX_PROPERTY = 500 * 1024
MAX_OCCURRENCES = 1_000
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


class CalendarParseError(ValueError):
    """Only a stable code leaves this boundary; never include raw calendar text."""


class CalendarTime(BaseModel):
    kind: Literal["date", "floating", "zoned", "utc"]
    value: str = Field(max_length=64)
    tzid: str = Field(default="", max_length=100)

    @model_validator(mode="after")
    def valid_domain(self) -> CalendarTime:
        if self.kind == "date":
            year = date.fromisoformat(self.value).year
            if len(self.value) != 10 or self.tzid:
                raise ValueError("Invalid calendar date.")
        else:
            stamp = datetime.fromisoformat(self.value)
            year = stamp.year
            if "T" not in self.value or (self.kind == "utc") != (stamp.tzinfo is not None):
                raise ValueError("Invalid calendar time domain.")
            if self.kind == "zoned":
                try:
                    ZoneInfo(self.tzid)
                except (ValueError, ZoneInfoNotFoundError):
                    raise ValueError("Invalid calendar timezone.") from None
            elif self.tzid or (self.kind == "utc" and stamp.utcoffset() != timedelta(0)):
                raise ValueError("Invalid calendar timezone.")
        if not 3 <= year <= 9997:
            raise ValueError("Date is outside the supported calendar range.")
        return self

    def datetime(self) -> datetime:
        if self.kind == "date":
            return datetime.combine(date.fromisoformat(self.value), datetime.min.time())
        parsed = datetime.fromisoformat(self.value)
        return parsed.replace(tzinfo=ZoneInfo(self.tzid)) if self.kind == "zoned" else parsed

    def key(self) -> str:
        if self.kind == "date":
            return "D:" + self.value
        if self.kind == "floating":
            return "F:" + self.value
        return "U:" + self.datetime().astimezone(UTC).isoformat()


class CalendarComponent(BaseModel):
    format_version: Literal[1] = 1
    uid: str
    recurrence_id: str = ""
    sequence: int = 0
    title: str = "Untitled event"
    note: str = ""
    location: str = ""
    url: str = ""
    start: CalendarTime | None = None
    duration_seconds: int = 0
    cancelled: bool = False
    rrule: str = ""
    exdates: list[CalendarTime] = Field(default_factory=list)
    rdates: list[CalendarTime] = Field(default_factory=list)


def valid_wall(value: datetime) -> bool:
    """Nonexistent local times are not recurrence instances (RFC 5545)."""
    return value.tzinfo is None or value.astimezone(UTC).astimezone(value.tzinfo).replace(
        tzinfo=None
    ) == value.replace(tzinfo=None)


def _temporal(value: Any, tzid: str, warnings: set[str]) -> CalendarTime:
    if isinstance(value, datetime):
        if tzid:
            try:
                zone = ZoneInfo(tzid)
            except (ZoneInfoNotFoundError, ValueError):
                warnings.add("unknown_timezone")
                return CalendarTime(kind="floating", value=value.replace(tzinfo=None).isoformat())
            local = value.replace(tzinfo=zone, fold=0)
            if not valid_wall(local):
                raise CalendarParseError("malformed")
            return CalendarTime(
                kind="zoned", value=local.replace(tzinfo=None).isoformat(), tzid=tzid
            )
        if value.tzinfo is not None:
            return CalendarTime(kind="utc", value=value.astimezone(UTC).isoformat())
        return CalendarTime(kind="floating", value=value.isoformat())
    if isinstance(value, date):
        return CalendarTime(kind="date", value=value.isoformat())
    raise CalendarParseError("malformed")


def _property_time(component: Any, name: str, warnings: set[str]) -> CalendarTime | None:
    prop = component.get(name)
    if prop is None:
        return None
    if isinstance(prop, list):
        raise CalendarParseError("malformed")
    return _temporal(prop.dt, str(prop.params.get("TZID", "")), warnings)


def _dates(component: Any, name: str, warnings: set[str]) -> list[CalendarTime]:
    result = []
    values = component.get(name, [])
    if not isinstance(values, list):
        values = [values]
    for prop in values:
        for item in prop.dts:
            result.append(_temporal(item.dt, str(prop.params.get("TZID", "")), warnings))
            if len(result) > MAX_OCCURRENCES:
                raise CalendarParseError("limit")
    return result


def _text(value: Any, maximum: int, warnings: set[str], *, single_line: bool = False) -> str:
    text = _CONTROL.sub("", str(value or ""))
    if single_line:
        text = " ".join(text.split())
    if len(text) > maximum:
        warnings.add("text_truncated")
    return text[:maximum]


def _link(value: Any) -> str:
    # Each URL restriction is independently required for a safe rendered link.
    # pylint: disable=too-many-boolean-expressions
    text = str(value or "")
    try:
        parts = urlsplit(text)
        if (
            len(text) <= 2048
            and parts.scheme in {"http", "https"}
            and parts.hostname
            and parts.username is None
            and parts.password is None
            and not any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in text)
        ):
            return text
    except ValueError:
        pass
    return ""


def _framing(data: bytes) -> str:
    if len(data) > MAX_BYTES:
        raise CalendarParseError("too_large")
    try:
        text = data.decode("utf-8-sig").replace("\r\n", "\n")
    except UnicodeDecodeError:
        raise CalendarParseError("malformed") from None
    stack: list[str] = []
    count, property_size = 0, 0
    lines = text.strip().split("\n")
    if not lines or lines[0].upper() != "BEGIN:VCALENDAR" or lines[-1].upper() != "END:VCALENDAR":
        raise CalendarParseError("malformed")
    for line in lines:
        size = len(line.encode("utf-8"))
        property_size = property_size + size if line.startswith((" ", "\t")) else size
        if property_size > MAX_PROPERTY:
            raise CalendarParseError("limit")
        if line.upper().startswith("BEGIN:"):
            name = line[6:].upper()
            if name == "VCALENDAR" and (stack or count):
                raise CalendarParseError("malformed")
            stack.append(name)
            count += 1
            if count > MAX_COMPONENTS or len(stack) > 16:
                raise CalendarParseError("limit")
        elif line.upper().startswith("END:"):
            if not stack or stack.pop() != line[4:].upper():
                raise CalendarParseError("malformed")
    if stack:
        raise CalendarParseError("malformed")
    return text


def _event(event: Any, cancel_all: bool, warnings: set[str]) -> CalendarComponent:
    uid = str(event.get("UID", ""))
    if not uid.strip() or len(uid) > 512 or _CONTROL.search(uid) or "\n" in uid or "\r" in uid:
        raise CalendarParseError("malformed")
    cancelled = cancel_all or str(event.get("STATUS", "")).upper() == "CANCELLED"
    start = _property_time(event, "DTSTART", warnings)
    if start is None and not cancelled:
        raise CalendarParseError("malformed")
    recurrence = _property_time(event, "RECURRENCE-ID", warnings)
    if recurrence and event["RECURRENCE-ID"].params.get("RANGE"):
        # THISANDFUTURE requires different reconciliation semantics; never silently apply it.
        raise CalendarParseError("unsupported_recurrence")
    sequence = int(event.get("SEQUENCE", 0))
    if not 0 <= sequence < 2**31:
        raise CalendarParseError("malformed")
    duration = 86400 if start and start.kind == "date" else 0
    end = _property_time(event, "DTEND", warnings)
    if end and start:
        if (end.kind == "date") != (start.kind == "date"):
            raise CalendarParseError("malformed")
        first, last = start.datetime(), end.datetime()
        if first.tzinfo and last.tzinfo:
            first, last = first.astimezone(UTC), last.astimezone(UTC)
        duration = int((last - first).total_seconds())
    elif "DURATION" in event:
        value = event.decoded("DURATION")
        if not isinstance(value, timedelta):
            raise CalendarParseError("malformed")
        duration = int(value.total_seconds())
    if not 0 <= duration <= 366 * 86400:
        raise CalendarParseError("limit")
    if start and start.kind == "date" and (duration < 86400 or duration % 86400):
        raise CalendarParseError("malformed")
    rrule = event.get("RRULE")
    if isinstance(rrule, list):
        raise CalendarParseError("unsupported_recurrence")
    return CalendarComponent(
        uid=uid,
        recurrence_id=recurrence.key() if recurrence else "",
        sequence=sequence,
        title=_text(event.get("SUMMARY"), 200, warnings, single_line=True) or "Untitled event",
        note=_text(event.get("DESCRIPTION"), 2000, warnings),
        location=_text(event.get("LOCATION"), 500, warnings, single_line=True),
        url=_link(event.get("URL")),
        start=start,
        duration_seconds=duration,
        cancelled=cancelled,
        rrule=rrule.to_ical().decode("ascii") if rrule else "",
        exdates=_dates(event, "EXDATE", warnings),
        rdates=_dates(event, "RDATE", warnings),
    )


def parse_calendar(data: bytes) -> tuple[list[CalendarComponent], list[str]]:
    """Reject partial/malformed snapshots; never infer deletion from a partial parse."""
    text = _framing(data)
    warnings: set[str] = set()
    try:
        calendar = Calendar.from_ical(text)
        components = calendar.walk()
        if any(component.errors for component in components):
            raise CalendarParseError("malformed")
        cancel_all = str(calendar.get("METHOD", "")).upper() == "CANCEL"
        result = {}
        for component in components:
            if component.name != "VEVENT":
                continue
            parsed = _event(component, cancel_all, warnings)
            key = (parsed.uid, parsed.recurrence_id)
            if key in result:
                warnings.add("duplicate_components")
            result[key] = parsed
        return list(result.values()), sorted(warnings)
    except CalendarParseError:
        raise
    except (ValueError, TypeError, AttributeError, KeyError, OverflowError):
        raise CalendarParseError("malformed") from None
