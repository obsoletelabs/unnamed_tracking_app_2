"""Tag catalogue: private labels and aliases, and administrator-managed deployment defaults (#267).

Nothing here rewrites stored tags. The catalogue only changes how tags are labelled, grouped and
filtered when they are read. See wiki/docs/development/design/tag-catalogue.md.
"""

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.auth import get_current_admin, get_current_user
from src.database.models.tag_catalogue import (
    TAG_LABEL_LENGTH,
    DefaultTag,
    DefaultTagAlias,
    UserTag,
    UserTagAlias,
)
from src.database.models.user import User
from src.database.session import get_db
from src.features import tag_catalogue as catalogue

router = APIRouter(prefix="/api/tags", tags=["tags"], dependencies=[Depends(get_current_user)])
defaults_router = APIRouter(
    prefix="/api/tags/defaults", tags=["tags"], dependencies=[Depends(get_current_user)]
)


class TagName(BaseModel):
    name: str = Field(min_length=1, max_length=TAG_LABEL_LENGTH * 2)


class SuppressionRequest(BaseModel):
    tag: str = Field(min_length=1, max_length=TAG_LABEL_LENGTH * 2)
    suppressed: bool = True


def _failure(exc: catalogue.TagCatalogueError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=str(exc))


def _tag_out(
    tag: UserTag | DefaultTag, aliases: list[UserTagAlias] | list[DefaultTagAlias]
) -> dict[str, Any]:
    return {
        "id": str(tag.id),
        "label": tag.label,
        "key": tag.key,
        "kind": getattr(tag, "kind", "user"),
        "aliases": [{"id": str(a.id), "label": a.alias_label} for a in aliases],
    }


async def _user_tags_out(db: AsyncSession, user_id: UUID) -> list[dict[str, Any]]:
    tags, aliases = await catalogue.load_user_tags(db, user_id)
    return [_tag_out(tag, [a for a in aliases if a.tag_id == tag.id]) for tag in tags]


async def _default_tags_out(db: AsyncSession) -> list[dict[str, Any]]:
    tags = (
        await db.scalars(
            select(DefaultTag).where(DefaultTag.archived_at.is_(None)).order_by(DefaultTag.label)
        )
    ).all()
    aliases = (await db.scalars(select(DefaultTagAlias))).all()
    return [_tag_out(tag, [a for a in aliases if a.tag_id == tag.id]) for tag in tags]


@router.get("")
async def list_tags(
    db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)
) -> dict[str, Any]:
    """Private tags and the deployment defaults visible to this user."""
    resolver = await catalogue.load_resolver(db, current_user.id)
    return {
        "tags": await _user_tags_out(db, current_user.id),
        "defaults": await _default_tags_out(db),
        "suppressed": sorted(resolver.suppressed),
    }


@router.get("/resolution")
async def tag_resolution(
    db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)
) -> dict[str, Any]:
    """Folded raw spelling -> effective label, for clients that group tags themselves."""
    resolver = await catalogue.load_resolver(db, current_user.id)
    rules = {
        key: {"label": hit.label, "scope": hit.scope} for key, hit in resolver.default_tags.items()
    }
    rules = {key: rule for key, rule in rules.items() if key not in resolver.suppressed}
    rules.update(
        {key: {"label": h.label, "scope": h.scope} for key, h in resolver.user_tags.items()}
    )
    return {"rules": rules}


@router.get("/usage")
async def tag_usage(
    db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)
) -> dict[str, Any]:
    """Raw spellings in use, with how often, and what each currently resolves to."""
    resolver = await catalogue.load_resolver(db, current_user.id)
    counts = await catalogue.raw_tag_counts(db, current_user.id)
    return {
        "tags": [
            {"tag": raw, "uses": uses, "label": resolver.label_for(raw)}
            for raw, uses in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
        ]
    }


@router.get("/suggestions")
async def tag_suggestions(
    db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)
) -> dict[str, Any]:
    """Alias candidates for the user to accept one at a time; nothing is applied."""
    return {"suggestions": await catalogue.alias_suggestions(db, current_user.id)}


@router.post("/discover")
async def discover_tags(
    db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)
) -> dict[str, int]:
    """Create catalogue rows for the tags already on the user's records."""
    created = await catalogue.discover_source_tags(db, current_user.id)
    await db.commit()
    return {"created": created}


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_tag(
    body: TagName,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    try:
        tag = await catalogue.create_user_tag(db, current_user.id, body.name)
    except catalogue.TagCatalogueError as exc:
        raise _failure(exc) from exc
    await db.commit()
    return next(t for t in await _user_tags_out(db, current_user.id) if t["id"] == str(tag.id))


@router.patch("/{tag_id}")
async def rename_tag(
    tag_id: UUID,
    body: TagName,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    try:
        tag = await catalogue.rename_user_tag(db, current_user.id, tag_id, body.name)
    except catalogue.TagCatalogueError as exc:
        raise _failure(exc) from exc
    await db.commit()
    return next(t for t in await _user_tags_out(db, current_user.id) if t["id"] == str(tag.id))


@router.delete("/{tag_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_tag(
    tag_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    try:
        await catalogue.archive_user_tag(db, current_user.id, tag_id)
    except catalogue.TagCatalogueError as exc:
        raise _failure(exc) from exc
    await db.commit()


@router.post("/{tag_id}/aliases", status_code=status.HTTP_201_CREATED)
async def add_alias(
    tag_id: UUID,
    body: TagName,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, str]:
    try:
        alias = await catalogue.add_user_alias(db, current_user.id, tag_id, body.name)
    except catalogue.TagCatalogueError as exc:
        raise _failure(exc) from exc
    await db.commit()
    return {"id": str(alias.id), "label": alias.alias_label}


@router.delete("/{tag_id}/aliases/{alias_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_alias(
    tag_id: UUID,
    alias_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    try:
        await catalogue.remove_user_alias(db, current_user.id, tag_id, alias_id)
    except catalogue.TagCatalogueError as exc:
        raise _failure(exc) from exc
    await db.commit()


@router.put("/suppressions", status_code=status.HTTP_204_NO_CONTENT)
async def set_suppression(
    body: SuppressionRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    """Show one raw spelling as it is, instead of its deployment default (or restore it)."""
    try:
        await catalogue.set_suppression(db, current_user.id, body.tag, body.suppressed)
    except catalogue.TagCatalogueError as exc:
        raise _failure(exc) from exc
    await db.commit()


# ------------------------------------------------------------- deployment defaults ---


@defaults_router.post("", status_code=status.HTTP_201_CREATED)
async def create_default(
    body: TagName, db: AsyncSession = Depends(get_db), admin: User = Depends(get_current_admin)
) -> dict[str, Any]:
    del admin
    try:
        tag = await catalogue.create_default_tag(db, body.name)
    except catalogue.TagCatalogueError as exc:
        raise _failure(exc) from exc
    await db.commit()
    return next(t for t in await _default_tags_out(db) if t["id"] == str(tag.id))


@defaults_router.patch("/{tag_id}")
async def rename_default(
    tag_id: UUID,
    body: TagName,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> dict[str, Any]:
    del admin
    try:
        tag = await catalogue.rename_default_tag(db, tag_id, body.name)
    except catalogue.TagCatalogueError as exc:
        raise _failure(exc) from exc
    await db.commit()
    return next(t for t in await _default_tags_out(db) if t["id"] == str(tag.id))


@defaults_router.delete("/{tag_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_default(
    tag_id: UUID, db: AsyncSession = Depends(get_db), admin: User = Depends(get_current_admin)
) -> None:
    del admin
    try:
        await catalogue.archive_default_tag(db, tag_id)
    except catalogue.TagCatalogueError as exc:
        raise _failure(exc) from exc
    await db.commit()


@defaults_router.post("/{tag_id}/aliases", status_code=status.HTTP_201_CREATED)
async def add_default_alias(
    tag_id: UUID,
    body: TagName,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> dict[str, str]:
    del admin
    try:
        alias = await catalogue.add_default_alias(db, tag_id, body.name)
    except catalogue.TagCatalogueError as exc:
        raise _failure(exc) from exc
    await db.commit()
    return {"id": str(alias.id), "label": alias.alias_label}


@defaults_router.delete("/{tag_id}/aliases/{alias_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_default_alias(
    tag_id: UUID,
    alias_id: UUID,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> None:
    del admin
    try:
        await catalogue.remove_default_alias(db, tag_id, alias_id)
    except catalogue.TagCatalogueError as exc:
        raise _failure(exc) from exc
    await db.commit()
