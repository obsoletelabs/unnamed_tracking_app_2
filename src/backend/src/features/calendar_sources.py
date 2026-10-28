"""Subscription ownership, atomic snapshot reconciliation and bounded refresh scheduling."""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import settings
from src.core.crypto import decrypt_secret, encrypt_secret
from src.core.metrics import (
    DATA_OPERATION_DURATION,
    DATA_OPERATIONS,
    DATA_RECORDS,
    INTEGRATION_LAST_SUCCESS,
)
from src.database.models.calendar_source import (
    CalendarSource,
    CalendarSourceEvent,
    CalendarSourceOverride,
)
from src.database.models.user import User
from src.database.session import SessionLocal
from src.features.auth_rate_limits import allow_auth_request
from src.features.calendar_fetch import (
    CalendarFetchError,
    CalendarSnapshot,
    calendar_url,
    refresh_snapshot,
)
from src.features.calendar_ics import CalendarParseError
from src.features.job_runs import BACKOFF_SECONDS, MAX_RETRIES

TOMBSTONE_SECONDS = 30 * 86400


def require_subscriptions() -> None:
    if not settings.CALENDAR_SUBSCRIPTIONS_ENABLED:
        raise HTTPException(403, "Remote calendar subscriptions are disabled by this deployment.")


def read_source(source: CalendarSource) -> dict[str, Any]:
    """No URL, encrypted value, validators, UID or response text in source status."""
    return {
        key: getattr(source, key)
        for key in (
            "id",
            "name",
            "color",
            "hostname",
            "enabled",
            "refresh_minutes",
            "last_attempt_at",
            "last_success_at",
            "next_refresh_at",
            "last_status",
            "warnings",
            "consecutive_failures",
        )
    }


async def owned_source(
    db: AsyncSession, source_id: UUID, user_id: UUID, *, lock: bool = False
) -> CalendarSource:
    query = select(CalendarSource).where(
        CalendarSource.id == source_id, CalendarSource.user_id == user_id
    )
    if lock:
        query = query.with_for_update()
    source = await db.scalar(query.execution_options(populate_existing=True))
    if source is None:
        raise HTTPException(404, "Calendar subscription not found.")
    return source


async def reconcile(
    db: AsyncSession, source: CalendarSource, snapshot: CalendarSnapshot, now: int
) -> dict[str, int]:
    """One validated complete response is applied in the source's locked transaction."""
    counts = {"created": 0, "updated": 0, "removed": 0, "unchanged": 0}
    if snapshot.status != "not_modified":
        rows = (
            await db.scalars(
                select(CalendarSourceEvent).where(CalendarSourceEvent.source_id == source.id)
            )
        ).all()
        existing = {(row.uid, row.recurrence_id): row for row in rows}
        seen = set()
        for component in snapshot.components:
            key = (component.uid, component.recurrence_id)
            seen.add(key)
            data = component.model_dump(mode="json")
            digest = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
            row = existing.get(key)
            if row is None:
                row = CalendarSourceEvent(
                    source_id=source.id,
                    user_id=source.user_id,
                    uid=component.uid,
                    recurrence_id=component.recurrence_id,
                    sequence=component.sequence,
                    data=data,
                    content_sha256=digest,
                    last_seen_at=now,
                )
                db.add(row)
                counts["created"] += 1
            else:
                row.last_seen_at = now
                if component.sequence >= row.sequence:
                    counts["updated" if row.content_sha256 != digest else "unchanged"] += 1
                    row.sequence, row.data, row.content_sha256 = component.sequence, data, digest
                    row.removed_at = None
                else:
                    counts["unchanged"] += 1
        for key, row in existing.items():
            if key not in seen and row.removed_at is None:
                row.removed_at = now
                counts["removed"] += 1
        source.etag, source.last_modified = snapshot.etag, snapshot.last_modified
        source.content_sha256 = snapshot.digest
        source.warnings = snapshot.warnings
    source.last_status = snapshot.status
    source.last_success_at = now
    source.consecutive_failures = 0
    source.next_refresh_at = now + source.refresh_minutes * 60 + source.id.int % 60
    return counts


def _record_calendar_outcome(status: str, counts: dict[str, int], duration: float) -> None:
    DATA_OPERATION_DURATION.labels("calendar", "refresh").observe(duration)
    DATA_OPERATIONS.labels("calendar", "refresh", status).inc()
    if status in {"succeeded", "not_modified"}:
        INTEGRATION_LAST_SUCCESS.labels("calendar").set(time.time())
    for outcome, count in counts.items():
        if count:
            DATA_RECORDS.labels("calendar", "event", outcome).inc(count)


async def create_source(db: AsyncSession, user_id: UUID, values: dict[str, Any]) -> dict[str, Any]:
    started = time.monotonic()
    require_subscriptions()
    url, hostname = calendar_url(values.pop("url"))
    # Reject capped accounts before remote work; recheck under the owner lock below.
    if (
        len(
            (
                await db.scalars(select(CalendarSource.id).where(CalendarSource.user_id == user_id))
            ).all()
        )
        >= 10
    ):
        raise HTTPException(409, "You can subscribe to at most 10 calendars.")
    await preview_budget(db, user_id)
    try:
        snapshot = await refresh_snapshot(url, None, None)
    except (CalendarFetchError, CalendarParseError) as exc:
        raise HTTPException(
            400,
            {
                "code": str(exc),
                "message": "The calendar could not be safely loaded. No subscription was saved.",
            },
        ) from None
    # Serialize the cap check, including concurrent create requests on different workers.
    await db.scalar(select(User).where(User.id == user_id).with_for_update())
    sources = (
        await db.scalars(select(CalendarSource.id).where(CalendarSource.user_id == user_id))
    ).all()
    if len(sources) >= 10:
        raise HTTPException(409, "You can subscribe to at most 10 calendars.")
    source = CalendarSource(
        id=uuid4(), user_id=user_id, url_encrypted=encrypt_secret(url), hostname=hostname, **values
    )
    db.add(source)
    await db.flush()
    now = int(time.time())
    source.last_attempt_at = now
    counts = await reconcile(db, source, snapshot, now)
    await db.commit()
    _record_calendar_outcome(
        "not_modified" if snapshot.status == "not_modified" else "succeeded",
        counts,
        time.monotonic() - started,
    )
    return read_source(source)


async def preview_budget(db: AsyncSession, user_id: UUID) -> None:
    """Reuse the existing durable request limiter rather than per-worker counters."""
    allowed = await allow_auth_request(
        db, "calendar-preview", (("owner", str(user_id), 10),), interval=900
    )
    await db.commit()
    if not allowed:
        raise HTTPException(
            429, "Wait before validating more calendar URLs.", headers={"Retry-After": "900"}
        )


async def refresh_source(
    db: AsyncSession, source_id: UUID, user_id: UUID, *, manual: bool = True
) -> dict[str, Any]:
    # The lease and reconciliation state already make this a large state machine.
    # pylint: disable=too-many-locals
    started = time.monotonic()
    source = await owned_source(db, source_id, user_id, lock=True)
    require_subscriptions()
    now = int(time.time())
    if not source.enabled:
        raise HTTPException(409, "Enable the subscription before refreshing it.")
    if not manual and source.next_refresh_at > now:
        raise HTTPException(409, "This subscription is not due yet.")
    if source.lease_until > now:
        raise HTTPException(409, "This subscription is already refreshing.")
    if manual and source.last_manual_at and now - source.last_manual_at < 300:
        raise HTTPException(
            429,
            "Wait five minutes between manual refreshes.",
            headers={"Retry-After": str(300 - now + source.last_manual_at)},
        )
    nonce, revision = str(uuid4()), source.revision
    source.lease_token, source.lease_until = nonce, now + 60
    source.last_attempt_at = now
    if manual:
        source.last_manual_at = now
    # Only encrypted URL storage; never include decrypt errors or transport traces in responses.
    try:
        url = decrypt_secret(source.url_encrypted)
    except (RuntimeError, ValueError):
        source.last_status = "secret_unavailable"
        source.consecutive_failures += 1
        source.next_refresh_at = now + 86400
        source.lease_token, source.lease_until = None, 0
        await db.commit()
        _record_calendar_outcome("failed", {}, time.monotonic() - started)
        return read_source(source)
    etag, modified = source.etag, source.last_modified
    await db.commit()
    snapshot, failure = None, ""
    try:
        snapshot = await refresh_snapshot(url, etag, modified)
    except (CalendarFetchError, CalendarParseError) as exc:
        failure = str(exc)
    source = await owned_source(db, source_id, user_id, lock=True)
    if source.lease_token != nonce or source.revision != revision or not source.enabled:
        result = read_source(source)
        await db.rollback()
        return result
    now = int(time.time())
    source.lease_token, source.lease_until = None, 0
    counts: dict[str, int] = {}
    if snapshot is not None:
        counts = await reconcile(db, source, snapshot, now)
    else:
        source.last_status = failure
        source.consecutive_failures += 1
        source.next_refresh_at = now + (
            BACKOFF_SECONDS[min(source.consecutive_failures, len(BACKOFF_SECONDS)) - 1]
            if source.consecutive_failures <= MAX_RETRIES
            else source.refresh_minutes * 60
        )
    await db.commit()
    _record_calendar_outcome(
        "failed"
        if snapshot is None
        else "not_modified"
        if snapshot.status == "not_modified"
        else "succeeded",
        counts,
        time.monotonic() - started,
    )
    return read_source(source)


async def run_due_sources() -> dict[str, Any]:
    """Use existing maintenance scheduling; each source has its own cross-worker lease."""
    now = int(time.time())
    checked, failed = 0, 0
    async with SessionLocal() as db:
        expired = (
            await db.scalars(
                select(CalendarSourceEvent)
                .where(CalendarSourceEvent.removed_at < now - TOMBSTONE_SECONDS)
                .order_by(CalendarSourceEvent.removed_at)
                .limit(1000)
            )
        ).all()
        for row in expired:
            query = delete(CalendarSourceOverride).where(
                CalendarSourceOverride.source_id == row.source_id,
                CalendarSourceOverride.uid == row.uid,
            )
            if row.recurrence_id:
                query = query.where(CalendarSourceOverride.recurrence_id == row.recurrence_id)
            await db.execute(query)
            await db.delete(row)
        if not settings.CALENDAR_SUBSCRIPTIONS_ENABLED:
            await db.commit()
            return {"checked": 0, "failed": 0}
        due = (
            await db.execute(
                select(CalendarSource.id, CalendarSource.user_id)
                .join(User)
                .where(
                    CalendarSource.enabled.is_(True),
                    User.is_active.is_(True),
                    CalendarSource.next_refresh_at <= now,
                    CalendarSource.lease_until <= now,
                )
                .order_by(CalendarSource.next_refresh_at)
                .limit(10)
            )
        ).all()
        await db.commit()
        for source_id, user_id in due:
            try:
                result = await refresh_source(db, source_id, user_id, manual=False)
                checked += 1
                failed += result["last_status"] not in {"updated", "not_modified"}
            except HTTPException:
                await db.rollback()
    return {"checked": checked, "failed": failed}
