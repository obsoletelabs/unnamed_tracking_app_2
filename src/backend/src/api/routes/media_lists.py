"""Custom lists across movies, TV shows and anime: manual lists (ordered,
with a pickable cover) and smart lists (a saved filter evaluated against
the whole library on every read — the media-side twin of the Games
side's Smart Collections)."""

import asyncio
import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.functions import max as sql_max

from src.api.routes.media_extras import _MODEL_BY_TYPE, _resolve_media
from src.api.routes.utils.media_lists import item_dict as _item_dict
from src.api.routes.utils.media_lists import resolve_items
from src.api.schemas.media_extras import (
    MediaListCreate,
    MediaListDetailRead,
    MediaListItemCreate,
    MediaListItemRead,
    MediaListRead,
    MediaListReorder,
    MediaListsOrder,
    MediaListUpdate,
)
from src.core.auth import get_current_user
from src.core.preferences import load_preferences
from src.database.models.media_extras import MediaList, MediaListItem, MediaType
from src.database.models.user import User
from src.database.session import get_db
from src.helpers.async_storage import finish_storage_operation
from src.helpers.media_collection_artwork import cover_metadata, remove_collection_files

router = APIRouter(prefix="/api", tags=["media-lists"], dependencies=[Depends(get_current_user)])


async def _resolve_items(lst: MediaList, user_id: UUID, db: AsyncSession) -> list[dict]:
    return (await resolve_items([lst], user_id, db, _MODEL_BY_TYPE))[lst.id]


def _summary(lst: MediaList, items: list[dict]) -> dict:
    ordered = list(items)
    if lst.cover_media_id is not None:
        ordered.sort(key=lambda i: 0 if i["media_id"] == lst.cover_media_id else 1)
    return {
        **cover_metadata(lst, ordered),
        "id": lst.id,
        "name": lst.name,
        "description": lst.description,
        "item_count": len(items),
        "is_smart": lst.smart_rule is not None,
        "is_system": lst.is_system,
        "pinned": lst.pinned,
        "position": lst.position,
        "type_counts": {
            t: sum(1 for i in items if i["media_type"] == t) for t in ("movie", "tv", "anime")
        },
        "smart_rule": lst.smart_rule,
        "cover_media_id": lst.cover_media_id,
        "preview_posters": [i["poster_url"] for i in ordered[:4]],
        "created_at": lst.created_at,
        "updated_at": lst.updated_at,
    }


async def _get_list_or_404(list_id: UUID, user_id: UUID, db: AsyncSession) -> MediaList:
    lst = await db.scalar(
        select(MediaList).where(MediaList.id == list_id, MediaList.user_id == user_id)
    )
    if lst is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"List {list_id} not found"
        )
    return lst


async def _ensure_favorites_list(user_id: UUID, db: AsyncSession) -> None:
    """Everyone gets a Favorites list that fills itself from the favorite
    flag on every title, so starring something is enough to file it."""
    exists = await db.scalar(
        select(MediaList.id)
        .where(MediaList.user_id == user_id, MediaList.is_system.is_(True))
        .limit(1)
    )
    if exists is not None:
        return
    # two requests can get here together (the lists page fires several at
    # once); the unique name makes the loser a no-op instead of an error
    await db.execute(
        pg_insert(MediaList)
        .values(
            user_id=user_id,
            name="Favorites",
            description="Everything you have marked as a favorite",
            smart_rule={"favorite": True},
            is_system=True,
        )
        .on_conflict_do_nothing(constraint="uq_media_lists_user_id_name")
    )
    # a list the user made by hand under the same name blocked the insert:
    # when it is the same favorites filter, it becomes the system list
    own = await db.scalar(
        select(MediaList).where(
            MediaList.user_id == user_id,
            MediaList.name == "Favorites",
            MediaList.is_system.is_(False),
        )
    )
    if own is not None and own.smart_rule == {"favorite": True}:
        own.is_system = True
    await db.commit()


@router.get("/lists", response_model=list[MediaListRead])
async def list_media_lists(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    *,
    include_previews: bool = Query(default=True),
) -> list[dict]:
    await _ensure_favorites_list(current_user.id, db)
    lists = (
        (
            await db.execute(
                select(MediaList)
                .where(MediaList.user_id == current_user.id)
                .order_by(
                    MediaList.pinned.desc(),
                    MediaList.position,
                    MediaList.is_system.desc(),
                    MediaList.name,
                )
            )
        )
        .scalars()
        .all()
    )
    items = await resolve_items(lists, current_user.id, db, _MODEL_BY_TYPE)
    summaries = await asyncio.to_thread(lambda: [_summary(lst, items[lst.id]) for lst in lists])
    if not include_previews:
        for summary in summaries:
            summary["preview_posters"] = []
    return summaries


@router.get("/lists/membership")
async def get_list_membership(
    media_type: str = Query(...),
    media_id: UUID = Query(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[dict]:
    """Which of the user's manual lists already contain this title, and
    the item row's own id — powers the checkmarks in the "Add to list"
    popover so it reads as a toggle (add/remove). Smart lists have no
    membership rows, so they never appear here."""
    rows = (
        await db.execute(
            select(MediaListItem.list_id, MediaListItem.id)
            .join(MediaList, MediaList.id == MediaListItem.list_id)
            .where(
                MediaList.user_id == current_user.id,
                MediaListItem.media_type == media_type,
                MediaListItem.media_id == media_id,
            )
        )
    ).all()
    return [{"list_id": list_id, "item_id": item_id} for list_id, item_id in rows]


@router.put("/lists/order", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def order_media_lists(
    payload: MediaListsOrder,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    """Saves the user's own order of their lists: each id's place in the
    request becomes its position. Ids that are not the caller's are ignored,
    and lists left out of the request keep their relative order after them."""
    lists = (
        (await db.execute(select(MediaList).where(MediaList.user_id == current_user.id)))
        .scalars()
        .all()
    )
    by_id = {lst.id: lst for lst in lists}
    ordered = [by_id[i] for i in dict.fromkeys(payload.list_ids) if i in by_id]
    left_out = sorted(
        (lst for lst in lists if lst not in ordered), key=lambda lst: (lst.position, lst.name)
    )
    for position, lst in enumerate([*ordered, *left_out]):
        lst.position = position
    await db.commit()


@router.post("/lists", response_model=MediaListRead, status_code=status.HTTP_201_CREATED)
async def create_media_list(
    payload: MediaListCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    last = await db.scalar(
        select(sql_max(MediaList.position)).where(MediaList.user_id == current_user.id)
    )
    lst = MediaList(
        user_id=current_user.id,
        position=(last or 0) + 1,
        name=payload.name,
        description=payload.description,
        smart_rule=payload.smart_rule.model_dump(exclude_none=True) if payload.smart_rule else None,
    )
    db.add(lst)
    await db.commit()
    await db.refresh(lst)
    return await asyncio.to_thread(_summary, lst, await _resolve_items(lst, current_user.id, db))


@router.patch("/lists/{list_id}", response_model=MediaListRead)
async def update_media_list(
    list_id: UUID,
    payload: MediaListUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    lst = await _get_list_or_404(list_id, current_user.id, db)
    updates = payload.model_dump(exclude_unset=True)
    if lst.is_system and ({"name", "smart_rule"} & updates.keys()):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Favorites is kept by the app; only its description and cover can change.",
        )
    if "smart_rule" in updates:
        rule = payload.smart_rule
        updates["smart_rule"] = rule.model_dump(exclude_none=True) if rule else None
    if updates.get("pinned", True) is None:
        del updates["pinned"]
    for field, value in updates.items():
        setattr(lst, field, value)
    await db.commit()
    await db.refresh(lst)
    return await asyncio.to_thread(_summary, lst, await _resolve_items(lst, current_user.id, db))


@router.delete("/lists/{list_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def delete_media_list(
    list_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    lst = await _get_list_or_404(list_id, current_user.id, db)
    if lst.is_system:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Favorites can't be deleted."
        )
    await db.delete(lst)
    await db.commit()
    try:
        await finish_storage_operation(
            asyncio.to_thread(remove_collection_files, current_user.id, list_id)
        )
    except OSError:
        logging.getLogger(__name__).warning("Deleted collection cover cleanup failed")


@router.get("/lists/{list_id}", response_model=MediaListDetailRead)
async def get_media_list(
    list_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    lst = await _get_list_or_404(list_id, current_user.id, db)
    items = await _resolve_items(lst, current_user.id, db)
    return {**(await asyncio.to_thread(_summary, lst, items)), "items": items}


@router.post(
    "/lists/{list_id}/items", response_model=MediaListItemRead, status_code=status.HTTP_201_CREATED
)
async def add_list_item(
    list_id: UUID,
    payload: MediaListItemCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    lst = await _get_list_or_404(list_id, current_user.id, db)
    if lst.smart_rule is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A smart list fills itself from its rule — titles can't be added by hand.",
        )
    media = await _resolve_media(payload.media_type, payload.media_id, current_user.id, db)
    item = await db.scalar(
        select(MediaListItem).where(
            MediaListItem.list_id == list_id,
            MediaListItem.media_type == payload.media_type,
            MediaListItem.media_id == payload.media_id,
        )
    )
    if item is None:
        # new titles go to the end of a hand-ordered list
        last = await db.scalar(
            select(MediaListItem.position)
            .where(MediaListItem.list_id == list_id)
            .order_by(MediaListItem.position.desc())
            .limit(1)
        )
        item = MediaListItem(
            list_id=list_id,
            media_type=MediaType(payload.media_type),
            media_id=payload.media_id,
            position=(last or 0) + 1,
        )
        db.add(item)
        await db.commit()
        await db.refresh(item)
    language = str((await load_preferences(db, current_user.id))["title_language"])
    return _item_dict(item.id, item.media_type, media, item.added_at, language)


@router.put("/lists/{list_id}/order", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def reorder_list_items(
    list_id: UUID,
    payload: MediaListReorder,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    """Sets the manual order: `item_ids` is the full desired sequence."""
    await _get_list_or_404(list_id, current_user.id, db)
    items = (
        (await db.execute(select(MediaListItem).where(MediaListItem.list_id == list_id)))
        .scalars()
        .all()
    )
    by_id = {i.id: i for i in items}
    for position, item_id in enumerate(payload.item_ids):
        item = by_id.get(item_id)
        if item is not None:
            item.position = position
    await db.commit()


@router.delete(
    "/lists/{list_id}/items/{item_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None
)
async def remove_list_item(
    list_id: UUID,
    item_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    await _get_list_or_404(list_id, current_user.id, db)
    item = await db.scalar(
        select(MediaListItem).where(MediaListItem.id == item_id, MediaListItem.list_id == list_id)
    )
    if item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"List item {item_id} not found"
        )
    await db.delete(item)
    await db.commit()
