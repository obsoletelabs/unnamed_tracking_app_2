"""In-app inbox APIs, with shared discovery from polling and the existing jobs loop."""

import time
from collections.abc import Sequence
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.functions import count as sql_count

from src.core.auth import get_current_admin, get_current_user
from src.core.preferences import load_preferences
from src.core.titles import display_title
from src.database.models.anime import Anime
from src.database.models.notification import Notification
from src.database.models.user import User
from src.database.session import get_db
from src.features.notification_audit import change_notices
from src.features.notification_controller import emit_legacy_rows
from src.features.notification_inbox import InboxQuery, query_inbox, retention_policy
from src.features.notification_lifecycle import delete_notice, dismiss, visible_inbox
from src.features.notifications import generate_for_user

_NOTIFICATION_DB = Depends(get_db)
_NOTIFICATION_USER = Depends(get_current_user)
_NOTIFICATION_ADMIN = Depends(get_current_admin)

router = APIRouter(
    prefix="/api/notifications", tags=["notifications"], dependencies=[Depends(get_current_user)]
)


def _read(n: Notification) -> dict:
    return {
        "id": n.id,
        "kind": n.kind,
        "media_type": n.media_type,
        "media_id": n.media_id,
        "title": n.title,
        "body": n.body,
        "poster_url": f"/api/media-image/{n.media_type}/{n.media_id}/poster"
        if n.poster_url
        else None,
        "event_at": n.event_at,
        "read": n.read_at is not None,
        "read_at": n.read_at,
        "created_at": n.created_at,
        "event_type": n.event_type,
        "source": n.source,
        "severity": n.severity,
        "purpose": n.purpose,
        "required_trust": n.required_trust,
        "group_key": n.group_key,
    }


async def _display_titles(
    db: AsyncSession, user_id: UUID, rows: Sequence[Notification]
) -> dict[UUID, str]:
    """A notification stores the title as it was when it was created. Anime
    titles are shown in the spelling the user picked now, so changing the
    setting also changes the ones already in the list."""
    anime_ids = {n.media_id for n in rows if n.media_type == "anime" and n.media_id}
    if not anime_ids:
        return {}
    language = str((await load_preferences(db, user_id))["title_language"])
    shows = (
        (await db.execute(select(Anime).where(Anime.id.in_(anime_ids), Anime.user_id == user_id)))
        .scalars()
        .all()
    )
    by_id = {s.id: display_title(s, language) for s in shows}
    return {
        n.id: by_id[n.media_id] for n in rows if n.media_type == "anime" and n.media_id in by_id
    }


@router.get("/unread-count")
async def unread_count(
    db: AsyncSession = _NOTIFICATION_DB, current_user: User = _NOTIFICATION_USER
) -> dict:
    await generate_for_user(db, current_user.id)
    count = await db.scalar(
        select(sql_count())
        .select_from(Notification)
        .where(visible_inbox(current_user.id), Notification.read_at.is_(None))
    )
    return {"unread": count or 0}


@router.get("")
async def list_notifications(
    filters: Annotated[InboxQuery, Query()],
    db: AsyncSession = _NOTIFICATION_DB,
    current_user: User = _NOTIFICATION_USER,
) -> dict:
    await generate_for_user(db, current_user.id)
    rows, summary = await query_inbox(db, current_user.id, filters)
    titles = await _display_titles(db, current_user.id, rows)
    return {
        "items": [{**_read(n), "title": titles.get(n.id, n.title)} for n in rows],
        **summary,
    }


@router.get("/policy")
async def notification_inbox_policy(
    db: AsyncSession = _NOTIFICATION_DB, current_user: User = _NOTIFICATION_USER
) -> dict:
    return retention_policy(await load_preferences(db, current_user.id))


@router.post("/regenerate")
async def regenerate_notifications(
    db: AsyncSession = _NOTIFICATION_DB,
    current_user: User = _NOTIFICATION_ADMIN,
) -> dict:
    """Admin-only: runs the same generation the bell's poll triggers, on
    demand, so a just-edited air date or release date doesn't need a poll
    cycle to show up while testing."""
    created = await generate_for_user(db, current_user.id)
    return {"created": created}


class TestNotificationRequest(BaseModel):
    kind: Literal["episode_aired", "season_started", "sequel_announced", "movie_released"]
    media_type: Literal["movie", "tv", "anime"]
    title: str
    body: str = ""


@router.post("/test", status_code=status.HTTP_201_CREATED)
async def create_test_notification(
    payload: TestNotificationRequest,
    db: AsyncSession = _NOTIFICATION_DB,
    current_user: User = _NOTIFICATION_ADMIN,
) -> dict:
    """Admin-only: fabricates a real notification row for your own account
    so the bell, the list, and read/unread/delete can all be exercised
    without waiting on an actual episode to air or movie to release. The
    media id is a random placeholder, so it won't link to a real page."""
    now = int(time.time())
    notification_ids = await emit_legacy_rows(
        db,
        current_user.id,
        [
            {
                "kind": payload.kind,
                "media_type": payload.media_type,
                "media_id": uuid4(),
                "title": payload.title,
                "body": payload.body,
                "poster_url": None,
                "event_at": now,
                "dedupe_key": f"test:{uuid4()}",
            }
        ],
        source="host.test",
    )
    if not notification_ids:
        raise HTTPException(status_code=409, detail="Notification disabled by preferences")
    notification = await db.get(Notification, notification_ids[0])
    assert notification is not None
    await db.commit()
    return _read(notification)


@router.post("/read-all", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def mark_all_read(
    db: AsyncSession = _NOTIFICATION_DB, current_user: User = _NOTIFICATION_USER
) -> None:
    await change_notices(
        db,
        update(Notification)
        .where(visible_inbox(current_user.id), Notification.read_at.is_(None))
        .values(read_at=int(time.time())),
        "read",
    )
    await db.commit()


@router.post("/{notification_id}/read", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def mark_read(
    notification_id: UUID,
    db: AsyncSession = _NOTIFICATION_DB,
    current_user: User = _NOTIFICATION_USER,
) -> None:
    count = await change_notices(
        db,
        update(Notification)
        .where(Notification.id == notification_id, visible_inbox(current_user.id))
        .values(read_at=int(time.time())),
        "read",
    )
    if not count:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found")
    await db.commit()


@router.post(
    "/{notification_id}/unread", status_code=status.HTTP_204_NO_CONTENT, response_model=None
)
async def mark_unread(
    notification_id: UUID,
    db: AsyncSession = _NOTIFICATION_DB,
    current_user: User = _NOTIFICATION_USER,
) -> None:
    count = await change_notices(
        db,
        update(Notification)
        .where(Notification.id == notification_id, visible_inbox(current_user.id))
        .values(read_at=None),
        "unread",
    )
    if not count:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found")
    await db.commit()


@router.delete("/{notification_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def delete_notification(
    notification_id: UUID,
    db: AsyncSession = _NOTIFICATION_DB,
    current_user: User = _NOTIFICATION_USER,
) -> None:
    await delete_notice(db, current_user.id, notification_id)
    await db.commit()


@router.post(
    "/{notification_id}/dismiss", status_code=status.HTTP_204_NO_CONTENT, response_model=None
)
async def dismiss_notification(
    notification_id: UUID,
    db: AsyncSession = _NOTIFICATION_DB,
    current_user: User = _NOTIFICATION_USER,
) -> None:
    await dismiss(db, current_user.id, notification_id)
    await db.commit()
