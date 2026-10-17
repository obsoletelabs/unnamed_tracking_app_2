"""Notification diagnostics: administrator dry runs and test sends, owner delivery history.

Administrator tools evaluate fixtures against the administrator's own destinations only. They
never read another account's destinations or content, accept no free-form content, and refuse to
fabricate recovery or verification messages. Dry-run endpoints roll their session back; only
`test-send` queues work, and only after explicit confirmation.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.auth import get_current_admin, get_current_user
from src.database.models.user import User
from src.database.session import get_db
from src.features import notification_diagnostics as diagnostics
from src.features.notification_diagnostic_fixtures import (
    FAILURES,
    FIXTURES,
    REDACTED_SENSITIVE,
    SENSITIVE_PURPOSES,
    VARIANTS,
)
from src.features.notification_enrollment import EnrollmentError
from src.features.notification_tests import MAX_TEST_BATCH, queue_destination_test

router = APIRouter(prefix="/api/settings/notification-diagnostics", tags=["settings"])
_DB = Depends(get_db)
_USER = Depends(get_current_user)
_ADMIN = Depends(get_current_admin)
Variant = Literal["basic", "rich", "image", "actions", "long"]
Failure = Literal["none", "transient", "rate_limited", "unavailable", "invalid_config", "permanent"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SimulateRequest(Strict):
    fixture: str = Field(max_length=64)
    destination_id: UUID | None = None
    at: int | None = Field(default=None, ge=0, le=2**40)
    duplicates: int | None = Field(default=None, ge=1, le=20)
    failure: Failure | None = None
    severity: Literal["info", "warning", "critical"] | None = None
    urgency: Literal["normal", "critical"] | None = None


class PreviewRequest(Strict):
    variant: Variant = "rich"
    provider_id: str | None = Field(default=None, max_length=128)
    fixture: str = Field(default="standard", max_length=64)


class DigestRequest(Strict):
    event_type: str = Field(pattern=r"^[a-zA-Z0-9._:-]{1,200}$")
    count: int = Field(default=12, ge=1, le=diagnostics.MAX_DIGEST_ITEMS)
    provider_id: str = Field(max_length=128)
    at: int | None = Field(default=None, ge=0, le=2**40)


class TestSendRequest(Strict):
    __test__ = False  # not a pytest class

    destination_id: UUID
    variant: Variant = "basic"
    batch_size: int = Field(default=1, ge=1, le=MAX_TEST_BATCH)
    fixture: str = Field(default="standard", max_length=64)
    confirm: Literal[True]


def _fixture(name: str) -> dict:
    spec = FIXTURES.get(name)
    if spec is None:
        raise HTTPException(404, "Unknown fixture")
    return spec


async def _dry(db: AsyncSession, operation):
    """Run a read-only evaluation and always discard the session's state afterwards."""
    try:
        return await operation
    finally:
        await db.rollback()


@router.get("/overview")
async def overview(db: AsyncSession = _DB, _admin: User = _ADMIN) -> dict:
    return await _dry(
        db,
        _overview(db),
    )


async def _overview(db: AsyncSession) -> dict:
    return {
        "fixtures": diagnostics.fixture_catalog(),
        "variants": list(VARIANTS),
        "failures": list(FAILURES),
        "providers": await diagnostics.provider_overview(db),
        "retry_policy": diagnostics.retry_policy(),
        "max_test_batch": MAX_TEST_BATCH,
    }


@router.post("/simulate")
async def simulate(payload: SimulateRequest, db: AsyncSession = _DB, admin: User = _ADMIN) -> dict:
    _fixture(payload.fixture)
    overrides = payload.model_dump(exclude={"fixture", "destination_id", "at"}, exclude_none=True)
    return await _dry(
        db,
        diagnostics.simulate(
            db,
            admin.id,
            payload.fixture,
            destination_id=payload.destination_id,
            at=payload.at,
            overrides=overrides,
        ),
    )


@router.post("/preview")
async def preview(payload: PreviewRequest, db: AsyncSession = _DB, _admin: User = _ADMIN) -> dict:
    spec = _fixture(payload.fixture)
    if spec.get("purpose") in SENSITIVE_PURPOSES:
        # Sensitive flows are explained, never rendered.
        return {"dry_run": True, "redacted": True, "message": REDACTED_SENSITIVE, "providers": []}
    return {
        "dry_run": True,
        "redacted": False,
        "variant": payload.variant,
        "providers": await _dry(
            db, diagnostics.render_previews(db, payload.variant, payload.provider_id)
        ),
    }


@router.post("/digest")
async def digest(payload: DigestRequest, db: AsyncSession = _DB, _admin: User = _ADMIN) -> dict:
    return await _dry(
        db,
        diagnostics.digest_preview(
            db, payload.event_type, payload.count, payload.provider_id, payload.at
        ),
    )


@router.post("/test-send", status_code=202)
async def test_send(payload: TestSendRequest, db: AsyncSession = _DB, admin: User = _ADMIN) -> dict:
    spec = _fixture(payload.fixture)
    if not spec.get("sendable", True) or spec.get("purpose") in SENSITIVE_PURPOSES:
        raise HTTPException(422, "Recovery and verification flows cannot be test-sent")
    try:
        queued = await queue_destination_test(
            db,
            admin.id,
            payload.destination_id,
            variant=payload.variant,
            batch_size=payload.batch_size,
        )
    except EnrollmentError as exc:
        await db.rollback()
        raise HTTPException(exc.status_code, str(exc)) from exc
    return {"labelled": "test", "dispatcher": "real", **queued}


@router.get("/deliveries")
async def deliveries(
    status: Literal[
        "pending", "processing", "sent", "retry_wait", "failed_permanent", "suppressed", "cancelled"
    ]
    | None = Query(default=None),
    limit: int = Query(default=25, ge=1, le=50),
    db: AsyncSession = _DB,
    user: User = _USER,
) -> dict:
    items = await _dry(db, diagnostics.user_deliveries(db, user.id, limit=limit, status=status))
    return {"deliveries": items, "retry_policy": diagnostics.retry_policy()}


@router.post("/deliveries/{delivery_id}/retry")
async def retry(delivery_id: UUID, db: AsyncSession = _DB, user: User = _USER) -> dict:
    try:
        return await diagnostics.retry_delivery(db, user.id, delivery_id)
    except diagnostics.RetryRefused as exc:
        await db.rollback()
        raise HTTPException(exc.status_code, str(exc)) from exc
