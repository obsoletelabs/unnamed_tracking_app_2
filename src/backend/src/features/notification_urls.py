"""Host-owned notification link resolution, isolated from OIDC and plugin credentials."""

from uuid import UUID

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.app_integrations import get_public_app_url
from src.core.preferences import load_preferences
from src.core.public_url import normalize_public_url
from src.database.models.notification_destination import NotificationDestination
from src.database.models.user import User


async def remember_app_url(db: AsyncSession, user: User, request: Request) -> None:
    """Called only after browser-session authentication, never from anonymous traffic."""
    if request.headers.get("sec-fetch-site") == "cross-site":
        return
    try:
        origin = normalize_public_url(str(request.base_url))
    except ValueError:
        return
    if origin and origin != user.last_app_url:
        user.last_app_url = origin
        await db.commit()


async def notification_url(
    db: AsyncSession, user_id: UUID, destination: NotificationDestination | None = None
) -> str:
    """Destination > personal preference > public deployment URL > last-used origin."""
    if destination is not None and destination.user_id != user_id:
        raise ValueError("Notification destination belongs to another user")
    preferences = await load_preferences(db, user_id)
    preferred = (destination.notification_url if destination else None) or preferences[
        "notification_url"
    ]
    if preferred:
        return normalize_public_url(preferred)
    configured = await get_public_app_url(db)
    if configured:
        return configured
    user = await db.get(User, user_id)
    return normalize_public_url(user.last_app_url or "") if user else ""
