"""Owner-scoped remote calendars and reversible event overrides (no write-back)."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.auth import get_current_user
from src.core.config import settings
from src.core.crypto import encrypt_secret
from src.database.models.calendar_event import CalendarEvent
from src.database.models.calendar_source import (
    CalendarSource,
    CalendarSourceEvent,
    CalendarSourceOverride,
)
from src.database.models.user import User
from src.database.session import get_db
from src.features.calendar_fetch import (
    CalendarFetchError,
    calendar_url,
    calendar_work,
    refresh_snapshot,
)
from src.features.calendar_ics import (
    CalendarComponent,
    CalendarParseError,
    CalendarTime,
    valid_wall,
)
from src.features.calendar_recurrence import expand_calendar
from src.features.calendar_sources import (
    create_source,
    owned_source,
    preview_budget,
    read_source,
    reconcile,
    refresh_source,
    require_subscriptions,
)

router = APIRouter(
    prefix="/api/calendar/sources", tags=["calendar"], dependencies=[Depends(get_current_user)]
)


class SourceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100)
    color: str = Field(default="#6b9bd2", pattern=r"^#[0-9a-fA-F]{6}$")
    url: str = Field(min_length=1, max_length=8192, repr=False)
    enabled: bool = True
    refresh_minutes: int = Field(default=360, ge=60, le=1440)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        if not value.strip() or any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("Give the subscription a name.")
        return value.strip()


class SourceUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=100)
    color: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    url: str | None = Field(default=None, min_length=1, max_length=8192, repr=False)
    enabled: bool | None = None
    refresh_minutes: int | None = Field(default=None, ge=60, le=1440)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str | None) -> str | None:
        return SourceCreate.clean_name(value) if value is not None else None


class OverrideFields(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(default=None, min_length=1, max_length=200)
    note: str | None = Field(default=None, max_length=2000)
    location: str | None = Field(default=None, max_length=500)
    start: CalendarTime | None = None
    duration_seconds: int | None = Field(default=None, ge=0, le=366 * 86400)

    @field_validator("title", "note", "location")
    @classmethod
    def plain_text(cls, value: str | None) -> str | None:
        if value is not None and any(
            (ord(char) < 32 and char not in "\n\t") or ord(char) == 127 for char in value
        ):
            raise ValueError("Calendar text cannot contain control characters.")
        return value


class OccurrenceTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_id: UUID
    recurrence_key: str = Field(default="", max_length=100)
    occurrence_date: date
    timezone: str = Field(default="UTC", max_length=100)

    @field_validator("occurrence_date")
    @classmethod
    def safe_date(cls, value: date) -> date:
        if not date(3, 1, 1) <= value <= date(9997, 12, 31):
            raise ValueError("Date is outside the supported calendar range.")
        return value


class OverrideUpdate(OccurrenceTarget):
    scope: Literal["occurrence", "series"] = "occurrence"
    fields: OverrideFields


def _zone(value: str) -> str:
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError):
        raise HTTPException(422, "Use a valid IANA timezone.") from None
    return value


async def _components(
    db: AsyncSession, source_id: UUID, user_id: UUID
) -> list[tuple[str, CalendarComponent]]:
    rows = (
        await db.scalars(
            select(CalendarSourceEvent).where(
                CalendarSourceEvent.source_id == source_id,
                CalendarSourceEvent.user_id == user_id,
                CalendarSourceEvent.removed_at.is_(None),
            )
        )
    ).all()
    return [(str(row.id), CalendarComponent.model_validate(row.data)) for row in rows]


async def _overlays(
    db: AsyncSession, source_id: UUID, user_id: UUID
) -> dict[tuple[str, str], dict[str, Any]]:
    rows = (
        await db.scalars(
            select(CalendarSourceOverride).where(
                CalendarSourceOverride.source_id == source_id,
                CalendarSourceOverride.user_id == user_id,
            )
        )
    ).all()
    return {(row.uid, row.recurrence_id): row.fields for row in rows}


@router.get("")
async def list_sources(
    db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)
) -> dict[str, Any]:
    rows = (
        await db.scalars(
            select(CalendarSource)
            .where(CalendarSource.user_id == current_user.id)
            .order_by(CalendarSource.created_at)
        )
    ).all()
    return {
        "remote_enabled": settings.CALENDAR_SUBSCRIPTIONS_ENABLED,
        "sources": [read_source(row) for row in rows],
    }


@router.post("", status_code=201)
async def add_source(
    payload: SourceCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    try:
        return await create_source(db, current_user.id, payload.model_dump())
    except CalendarFetchError:
        raise HTTPException(
            422, "Use an HTTPS calendar URL without credentials or a fragment, on port 443."
        ) from None


@router.patch("/{source_id}")
async def update_source(
    source_id: UUID,
    payload: SourceUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    # Check ownership before any network operation, including a replacement URL preview.
    await owned_source(db, source_id, current_user.id)
    values = payload.model_dump(exclude_unset=True)
    if any(value is None for value in values.values()) or (
        "name" in values and not str(values["name"]).strip()
    ):
        raise HTTPException(422, "Subscription fields cannot be empty.")
    snapshot = None
    if "url" in values:
        require_subscriptions()
        try:
            url, hostname = calendar_url(values.pop("url"))
            await preview_budget(db, current_user.id)
            snapshot = await refresh_snapshot(url, None, None)
            values.update(url_encrypted=encrypt_secret(url), hostname=hostname)
        except (CalendarFetchError, CalendarParseError) as exc:
            raise HTTPException(
                400,
                {
                    "code": str(exc),
                    "message": "Replacement calendar could not be loaded; the original was kept.",
                },
            ) from None
    source = await owned_source(db, source_id, current_user.id, lock=True)
    for key, value in values.items():
        setattr(source, key, value.strip() if key == "name" else value)
    source.revision += 1
    source.lease_token, source.lease_until = None, 0
    if snapshot is not None:
        # A different feed must not inherit SEQUENCE comparisons or private overrides.
        await db.execute(
            delete(CalendarSourceEvent).where(CalendarSourceEvent.source_id == source.id)
        )
        await db.execute(
            delete(CalendarSourceOverride).where(CalendarSourceOverride.source_id == source.id)
        )
        import time  # pylint: disable=import-outside-toplevel

        await reconcile(db, source, snapshot, int(time.time()))
    await db.commit()
    return read_source(source)


@router.delete("/{source_id}", status_code=204)
async def remove_source(
    source_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    source = await owned_source(db, source_id, current_user.id, lock=True)
    await db.delete(source)
    await db.commit()


@router.post("/{source_id}/refresh")
async def refresh(
    source_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    return await refresh_source(db, source_id, current_user.id)


@router.get("/{source_id}/events")
async def events(
    source_id: UUID,
    *,
    start: date,
    end: date,
    timezone: str = Query(default="UTC", max_length=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    source = await owned_source(db, source_id, current_user.id)
    if end < start or (end - start).days > 366 or start < date(3, 1, 1) or end > date(9997, 12, 31):
        raise HTTPException(422, "Choose a calendar window of at most 366 days.")
    components = await _components(db, source_id, current_user.id)
    overlays = await _overlays(db, source_id, current_user.id)
    entries, warnings = (
        await _expand(components, overlays, start, end, _zone(timezone))
        if source.enabled
        else ([], [])
    )
    return {
        "source": read_source(source),
        "events": entries,
        "warnings": sorted(set(source.warnings + warnings)),
    }


async def _target(
    db: AsyncSession, source_id: UUID, user_id: UUID, payload: OccurrenceTarget
) -> tuple[dict[str, Any], list[tuple[str, CalendarComponent]]]:
    source = await owned_source(db, source_id, user_id, lock=True)
    if not source.enabled:
        raise HTTPException(409, "Enable the subscription before editing an event.")
    components = await _components(db, source_id, user_id)
    selected = next(
        (component for row_id, component in components if row_id == str(payload.event_id)), None
    )
    if selected is None:
        raise HTTPException(404, "Calendar occurrence not found.")
    entries, _warnings = await _expand(
        components,
        {},
        payload.occurrence_date - timedelta(days=1),
        payload.occurrence_date + timedelta(days=1),
        _zone(payload.timezone),
    )
    entry = next(
        (
            entry
            for entry in entries
            if entry["uid"] == selected.uid and entry["recurrence_key"] == payload.recurrence_key
        ),
        None,
    )
    if entry is None:
        raise HTTPException(404, "Calendar occurrence is cancelled, removed or no longer exists.")
    return entry, components


async def _expand(components, overlays, start, end, timezone):
    try:
        return await calendar_work(
            lambda: expand_calendar(components, overlays, start, end, timezone)
        )
    except CalendarFetchError:
        raise HTTPException(503, "Calendar processing is busy; try again shortly.") from None


@router.put("/{source_id}/overrides")
async def set_override(
    source_id: UUID,
    payload: OverrideUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, bool]:
    entry, _items = await _target(db, source_id, current_user.id, payload)
    fields = payload.fields.model_dump(exclude_none=True)
    if (
        "duration_seconds" in fields
        and entry["original_start"]["kind"] == "date"
        and (fields["duration_seconds"] < 86400 or fields["duration_seconds"] % 86400)
    ):
        raise HTTPException(422, "All-day durations must be a positive whole number of days.")
    if "title" in fields:
        fields["title"] = fields["title"].strip()
        if not fields["title"]:
            raise HTTPException(422, "Give the event a title.")
    if payload.fields.start is not None:
        new = payload.fields.start
        try:
            stamp = new.datetime()
            if not valid_wall(stamp):
                raise ValueError
        except (ValueError, ZoneInfoNotFoundError):
            raise HTTPException(422, "The event time does not exist in that timezone.") from None
        original = CalendarTime.model_validate(entry["original_start"])
        if (new.kind == "date") != (original.kind == "date"):
            raise HTTPException(422, "Keep an all-day event all-day, or copy it to a manual event.")
        if (new.kind == "floating") != (original.kind == "floating"):
            raise HTTPException(422, "Keep floating times floating, or copy to a manual event.")
        if payload.scope == "series":
            original_stamp = original.datetime()
            if original_stamp.tzinfo and stamp.tzinfo:
                stamp = stamp.astimezone(original_stamp.tzinfo)
            shift = int(
                (stamp.replace(tzinfo=None) - original_stamp.replace(tzinfo=None)).total_seconds()
            )
            if abs(shift) > 366 * 86400:
                raise HTTPException(422, "Series time shifts are limited to 366 days.")
            fields.pop("start")
            fields["shift_seconds"] = shift
    key = "*" if payload.scope == "series" else payload.recurrence_key
    row = await db.scalar(
        select(CalendarSourceOverride).where(
            CalendarSourceOverride.source_id == source_id,
            CalendarSourceOverride.uid == entry["uid"],
            CalendarSourceOverride.recurrence_id == key,
        )
    )
    if row is None:
        row = CalendarSourceOverride(
            source_id=source_id, user_id=current_user.id, uid=entry["uid"], recurrence_id=key
        )
        db.add(row)
    row.fields = {**(row.fields or {}), **fields}
    await db.commit()
    return {"saved": True}


@router.delete("/{source_id}/overrides")
async def clear_override(
    source_id: UUID,
    payload: OverrideUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, bool]:
    entry, _items = await _target(db, source_id, current_user.id, payload)
    await db.execute(
        delete(CalendarSourceOverride).where(
            CalendarSourceOverride.source_id == source_id,
            CalendarSourceOverride.uid == entry["uid"],
            CalendarSourceOverride.recurrence_id
            == ("*" if payload.scope == "series" else payload.recurrence_key),
        )
    )
    await db.commit()
    return {"cleared": True}


@router.post("/{source_id}/copy", status_code=201)
async def copy_event(
    source_id: UUID,
    payload: OccurrenceTarget,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    entry, _components = await _target(db, source_id, current_user.id, payload)
    overlays = await _overlays(db, source_id, current_user.id)
    # Resolve the same occurrence even when a local override moved it outside the original day.
    fields = {
        **overlays.get((entry["uid"], "*"), {}),
        **overlays.get((entry["uid"], entry["recurrence_key"]), {}),
    }
    value = CalendarTime.model_validate(fields.get("start", entry["start"]))
    if "start" not in fields and fields.get("shift_seconds"):
        stamp = value.datetime() + timedelta(seconds=fields["shift_seconds"])
    else:
        stamp = value.datetime()
    if stamp.tzinfo:
        stamp = stamp.astimezone(ZoneInfo(payload.timezone))
    note = fields.get("note", entry["note"])
    location = fields.get("location", entry["location"])
    if location:
        note = (note + "\nLocation: " + location).strip()[:2000]
    event = CalendarEvent(
        user_id=current_user.id,
        title=fields.get("title", entry["title"]),
        event_date=stamp.date(),
        event_time=None if value.kind == "date" else stamp.strftime("%H:%M"),
        note=note,
    )
    db.add(event)
    await db.commit()
    return {"id": str(event.id)}
