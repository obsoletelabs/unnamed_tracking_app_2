"""Owner APIs for story pages, live links, proxied images and plugin story actions."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.auth import get_current_user
from src.database.models.notification_story import NotificationLiveLink
from src.database.models.user import User
from src.database.session import get_db
from src.features.notification_images import FALLBACK_ARTWORK, cached_image, effective_image_mode
from src.features.notification_story_pages import (
    ActionDenied,
    ActionUnavailable,
    StoryNotFound,
    StoryPageError,
    create_live_link,
    invoke_story_action,
    link_owner_view,
    list_live_links,
    live_url,
    owned_state,
    owner_view,
    parse_content,
    revoke_all_live_links,
    revoke_live_link,
)
from src.features.notification_urls import notification_url

PREFIX = "/api/notifications/stories"
router = APIRouter(prefix=PREFIX, tags=["notifications"], dependencies=[Depends(get_current_user)])
_DB = Depends(get_db)
_USER = Depends(get_current_user)
_PRIVATE_HEADERS = {"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"}


class LiveLinkRequest(BaseModel):
    days: int | None = Field(default=None, ge=1, le=365)


class ActionRequest(BaseModel):
    confirm: bool = False


def _link_read(link: NotificationLiveLink) -> dict:
    return {
        "id": link.id,
        "created_at": link.created_at,
        "expires_at": link.expires_at,
        "revoked_at": link.revoked_at,
        "last_used_at": link.last_used_at,
    }


def _not_found(_: StoryNotFound) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Story not found")


@router.post("/live-links/revoke-all")
async def revoke_all_links(db: AsyncSession = _DB, user: User = _USER) -> dict:
    count = await revoke_all_live_links(db, user.id)
    await db.commit()
    return {"revoked": count}


@router.get("/by-link/{token}")
async def story_by_link(
    token: Annotated[str, Path(max_length=512)],
    db: AsyncSession = _DB,
    user: User = _USER,
) -> Response:
    """Opening a live link while signed in as the owner shows the full private state."""
    try:
        data = await link_owner_view(db, user.id, token, proxy_prefix=PREFIX)
    except StoryNotFound as exc:
        raise _not_found(exc) from exc
    await db.commit()
    return _json(data)


@router.get("/{story_id}")
async def get_story(story_id: UUID, db: AsyncSession = _DB, user: User = _USER) -> Response:
    try:
        data = await owner_view(db, user.id, story_id, proxy_path=f"{PREFIX}/{story_id}/images")
    except StoryNotFound as exc:
        raise _not_found(exc) from exc
    return _json(data)


def _json(data: dict, code: int = 200) -> Response:
    return JSONResponse(jsonable_encoder(data), status_code=code, headers=_PRIVATE_HEADERS)


@router.post("/{story_id}/live-links", status_code=status.HTTP_201_CREATED)
async def make_live_link(
    story_id: UUID,
    payload: LiveLinkRequest,
    request: Request,
    db: AsyncSession = _DB,
    user: User = _USER,
) -> Response:
    try:
        link, token = await create_live_link(db, user.id, story_id, days=payload.days)
    except StoryNotFound as exc:
        raise _not_found(exc) from exc
    except StoryPageError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    origin = await notification_url(db, user.id) or str(request.base_url).rstrip("/")
    await db.commit()
    # The only time the bearer URL exists in plain form.
    return _json({**_link_read(link), "url": live_url(origin, token)}, status.HTTP_201_CREATED)


@router.get("/{story_id}/live-links")
async def get_live_links(story_id: UUID, db: AsyncSession = _DB, user: User = _USER) -> Response:
    try:
        links = await list_live_links(db, user.id, story_id)
    except StoryNotFound as exc:
        raise _not_found(exc) from exc
    return _json({"items": [_link_read(link) for link in links]})


@router.delete(
    "/{story_id}/live-links/{link_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None
)
async def delete_live_link(
    story_id: UUID, link_id: UUID, db: AsyncSession = _DB, user: User = _USER
) -> None:
    if not await revoke_live_link(db, user.id, story_id, link_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Link not found")
    await db.commit()


@router.get("/{story_id}/images/{index}")
async def story_image(
    story_id: UUID, index: int, db: AsyncSession = _DB, user: User = _USER
) -> Response:
    """Policy-selected, re-encoded copy of an external image; fallback artwork on any failure."""
    try:
        state = await owned_state(db, user.id, story_id)
    except StoryNotFound as exc:
        raise _not_found(exc) from exc
    content = parse_content(state.revision)
    image = content.images[index] if content and 0 <= index < len(content.images) else None
    if image is None or image.source.kind != "external":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Image not found")
    headers = {
        **_PRIVATE_HEADERS,
        "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'",
    }
    path = await cached_image(image.source.url) if effective_image_mode() == "proxied" else None
    if path is None:
        return Response(
            FALLBACK_ARTWORK,
            media_type="image/svg+xml",
            headers={**headers, "Cache-Control": "no-store"},
        )
    return FileResponse(
        path, media_type="image/jpeg", headers={**headers, "Cache-Control": "private, max-age=3600"}
    )


@router.post("/{story_id}/actions/{action_id}")
async def run_story_action(
    story_id: UUID,
    action_id: str,
    payload: ActionRequest,
    db: AsyncSession = _DB,
    user: User = _USER,
) -> Response:
    """Plugin actions run only here: the host rechecks everything and supplies the input."""
    try:
        result = await invoke_story_action(
            db, user.id, story_id, action_id, confirmed=payload.confirm
        )
    except StoryNotFound as exc:
        raise _not_found(exc) from exc
    except ActionDenied as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except ActionUnavailable as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    return _json(result)
