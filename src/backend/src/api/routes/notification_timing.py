"""Explain the effective notification timing for one event type and destination."""

from __future__ import annotations

import time
from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.auth import get_current_user
from src.core.preferences import load_preferences
from src.database.models.notification import Notification
from src.database.models.notification_destination import NotificationDestination
from src.database.models.user import User
from src.database.session import get_db
from src.features.notification_policy import route_choice
from src.features.notification_timing import decide_for
from src.features.notification_timing_policy import category_of

router = APIRouter(prefix="/api/settings/notification-timing", tags=["settings"])
_DB = Depends(get_db)
_USER = Depends(get_current_user)


@router.get("/preview")
# pylint: disable-next=too-many-positional-arguments
async def preview_timing(
    event_type: str = Query(pattern=r"^[a-zA-Z0-9._:-]{1,200}$"),
    destination_id: UUID = Query(),
    at: int | None = Query(default=None, ge=0, le=2**40),
    plugin: bool = Query(default=False),
    plugin_urgency: Literal["normal", "critical"] = Query(default="normal"),
    severity: Literal["info", "warning", "critical"] = Query(default="info"),
    db: AsyncSession = _DB,
    current_user: User = _USER,
) -> dict:
    """What would happen to a standard notification now (or at ``at``); nothing is queued."""
    destination = await db.scalar(
        select(NotificationDestination).where(
            NotificationDestination.id == destination_id,
            NotificationDestination.user_id == current_user.id,
        )
    )
    if destination is None:
        raise HTTPException(404, "Destination not found")
    preferences = await load_preferences(db, current_user.id)
    now = int(time.time()) if at is None else at
    route = route_choice(event_type, str(destination.id), preferences)
    result: dict = {
        "event_type": event_type,
        "destination_id": str(destination.id),
        "category": category_of(event_type, uuid4() if plugin else None),
        "evaluated_at": now,
        "route_enabled": bool(route["enabled"]),
        "route_urgency": route["urgency"],
    }
    if not route["enabled"]:
        # A disabled route owns no schedule: nothing is queued now or replayed later.
        result["decision"] = {
            "action": "none",
            "mode": "disabled",
            "release_at": None,
            "bypassed_by_urgency": False,
            "matched_rule": "route",
            "reasons": ["route: this type is disabled for this destination; nothing is queued"],
        }
        return result
    probe = Notification(
        event_type=event_type,
        kind="plugin" if plugin else event_type.rsplit(".", 1)[-1],
        purpose="standard",
        severity=severity,
        requested_urgency=plugin_urgency,
        source_installation_id=uuid4() if plugin else None,
        event_at=now,
        media_id=uuid4(),
        group_key=None,
    )
    result["decision"] = decide_for(probe, destination, preferences, now=now).explain()
    return result
