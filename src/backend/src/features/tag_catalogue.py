"""Tag catalogue rules: key folding, alias resolution and the operations behind /api/tags (#267).

Stored `tags` arrays are never rewritten. A raw spelling is folded to a key; private rows beat
deployment defaults, and a user can suppress a default for one spelling. Resolution is applied
when tags are read (statistics, filters, the picker map).
"""

from __future__ import annotations

import time
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.functions import count as sql_count

from src.database.models.anime import Anime
from src.database.models.game import Game
from src.database.models.game_archive import GameArchive
from src.database.models.game_file_item import GameFileItem
from src.database.models.game_note_detail import GameNoteDetail
from src.database.models.media_item import MediaItem
from src.database.models.movies import Movie
from src.database.models.tag_catalogue import (
    TAG_KEY_LENGTH,
    TAG_LABEL_LENGTH,
    DefaultTag,
    DefaultTagAlias,
    TagKind,
    UserTag,
    UserTagAlias,
    UserTagSuppression,
)
from src.database.models.tv_show import TVShow

SCOPE_USER = "user"
SCOPE_DEFAULT = "default"
SUGGESTION_SIMILARITY = 0.84
MAX_SUGGESTIONS = 50


class TagCatalogueError(ValueError):
    """A catalogue change that breaks a rule; `status` is the HTTP status the API reports."""

    def __init__(self, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.status = status


def fold_key(raw: str) -> str:
    """Comparison key: NFKC, case-folded, whitespace collapsed. Namespaces are kept as typed."""
    return " ".join(unicodedata.normalize("NFKC", raw).casefold().split())[:TAG_KEY_LENGTH]


def clean_label(raw: str) -> str:
    label = " ".join(unicodedata.normalize("NFC", raw).split())
    if not label:
        raise TagCatalogueError("A tag name cannot be empty.")
    if len(label) > TAG_LABEL_LENGTH:
        raise TagCatalogueError(f"A tag name is at most {TAG_LABEL_LENGTH} characters.")
    return label


@dataclass(frozen=True)
class Resolved:
    label: str
    scope: str
    tag_id: UUID


@dataclass
class TagResolver:
    """In-memory view of one user's effective catalogue (private rows over defaults)."""

    user_tags: dict[str, Resolved] = field(default_factory=dict)  # folded key or alias -> tag
    default_tags: dict[str, Resolved] = field(default_factory=dict)
    suppressed: set[str] = field(default_factory=set)
    spellings: dict[UUID, set[str]] = field(default_factory=dict)  # tag id -> raw spellings

    @property
    def is_empty(self) -> bool:
        return not self.user_tags and not self.default_tags

    def resolve(self, raw: str) -> Resolved | None:
        key = fold_key(raw)
        if (hit := self.user_tags.get(key)) is not None:
            return hit
        if key in self.suppressed:
            return None
        return self.default_tags.get(key)

    def label_for(self, raw: str) -> str:
        hit = self.resolve(raw)
        return hit.label if hit else raw

    def spellings_of(self, tag_id: UUID) -> set[str]:
        return self.spellings.get(tag_id, set())


async def load_resolver(db: AsyncSession, user_id: UUID) -> TagResolver:
    resolver = TagResolver()
    tags = (
        await db.scalars(
            select(UserTag).where(UserTag.user_id == user_id, UserTag.archived_at.is_(None))
        )
    ).all()
    by_id = {tag.id: tag for tag in tags}
    for tag in tags:
        resolver.user_tags[tag.key] = Resolved(tag.label, SCOPE_USER, tag.id)
        resolver.spellings.setdefault(tag.id, set()).add(tag.label)
    aliases = await db.scalars(select(UserTagAlias).where(UserTagAlias.user_id == user_id))
    for alias in aliases.all():
        if (owner := by_id.get(alias.tag_id)) is not None:
            resolver.user_tags[alias.alias_key] = Resolved(owner.label, SCOPE_USER, owner.id)
            resolver.spellings.setdefault(owner.id, set()).add(alias.alias_label)
    suppressions = await db.scalars(
        select(UserTagSuppression.tag_key).where(UserTagSuppression.user_id == user_id)
    )
    resolver.suppressed = set(suppressions.all())
    defaults = (await db.scalars(select(DefaultTag).where(DefaultTag.archived_at.is_(None)))).all()
    default_by_id = {item.id: item for item in defaults}
    for default in defaults:
        resolver.default_tags[default.key] = Resolved(default.label, SCOPE_DEFAULT, default.id)
        resolver.spellings.setdefault(default.id, set()).add(default.label)
    for default_alias in (await db.scalars(select(DefaultTagAlias))).all():
        if (parent := default_by_id.get(default_alias.tag_id)) is not None:
            resolver.default_tags[default_alias.alias_key] = Resolved(
                parent.label, SCOPE_DEFAULT, parent.id
            )
            resolver.spellings.setdefault(parent.id, set()).add(default_alias.alias_label)
    return resolver


def count_resolved_tags(
    rows: list[list[str]], resolver: TagResolver, limit: int
) -> list[tuple[str, int]]:
    """Count tagged records per effective label; one record counts once per label."""
    counts: dict[str, int] = {}
    for tags in rows:
        for label in {resolver.label_for(tag) for tag in tags}:
            counts[label] = counts.get(label, 0) + 1
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:limit]


def filter_spellings(resolver: TagResolver, tag: str) -> list[str]:
    """Every raw spelling that should match a filter on `tag`, including `tag` itself."""
    spellings = {tag}
    if (hit := resolver.resolve(tag)) is not None:
        spellings |= resolver.spellings_of(hit.tag_id)
    return sorted(spellings)


# ---------------------------------------------------------------- private catalogue ---


async def get_user_tag(db: AsyncSession, user_id: UUID, tag_id: UUID) -> UserTag:
    tag = await db.scalar(select(UserTag).where(UserTag.id == tag_id, UserTag.user_id == user_id))
    if tag is None or tag.archived_at is not None:
        raise TagCatalogueError("Tag not found.", 404)
    return tag


async def create_user_tag(
    db: AsyncSession,
    user_id: UUID,
    label: str,
    *,
    kind: TagKind = TagKind.USER,
    namespace: str | None = None,
) -> UserTag:
    """Create a tag, or return the existing one when the folded key already exists."""
    label = clean_label(label)
    key = fold_key(label)
    existing = await db.scalar(
        select(UserTag).where(UserTag.user_id == user_id, UserTag.key == key)
    )
    if existing is not None:
        if existing.archived_at is not None:
            existing.archived_at = None
            existing.label = label
        return existing
    taken = await db.scalar(
        select(UserTagAlias.id).where(
            UserTagAlias.user_id == user_id, UserTagAlias.alias_key == key
        )
    )
    if taken is not None:
        raise TagCatalogueError("That spelling is already an alias; remove the alias first.", 409)
    tag = UserTag(user_id=user_id, label=label, key=key, kind=kind.value, namespace=namespace)
    db.add(tag)
    await db.flush()
    return tag


async def rename_user_tag(db: AsyncSession, user_id: UUID, tag_id: UUID, label: str) -> UserTag:
    tag = await get_user_tag(db, user_id, tag_id)
    tag.label = clean_label(label)
    await db.flush()
    return tag


async def archive_user_tag(db: AsyncSession, user_id: UUID, tag_id: UUID) -> None:
    """Archive the tag and drop its aliases; raw tags on records are untouched."""
    tag = await get_user_tag(db, user_id, tag_id)
    tag.archived_at = int(time.time())
    await db.execute(
        delete(UserTagAlias).where(UserTagAlias.user_id == user_id, UserTagAlias.tag_id == tag.id)
    )
    await db.flush()


async def add_user_alias(db: AsyncSession, user_id: UUID, tag_id: UUID, raw: str) -> UserTagAlias:
    tag = await get_user_tag(db, user_id, tag_id)
    label = clean_label(raw)
    key = fold_key(label)
    if key == tag.key:
        raise TagCatalogueError("That spelling already names this tag.")
    other = await db.scalar(
        select(UserTag.id).where(
            UserTag.user_id == user_id, UserTag.key == key, UserTag.archived_at.is_(None)
        )
    )
    if other is not None:
        raise TagCatalogueError(
            "That spelling is a catalogue tag of its own; archive it before aliasing it.", 409
        )
    existing = await db.scalar(
        select(UserTagAlias).where(UserTagAlias.user_id == user_id, UserTagAlias.alias_key == key)
    )
    if existing is not None:
        if existing.tag_id != tag.id:
            raise TagCatalogueError("That spelling is already an alias of another tag.", 409)
        return existing
    alias = UserTagAlias(user_id=user_id, tag_id=tag.id, alias_key=key, alias_label=label)
    db.add(alias)
    await db.flush()
    return alias


async def remove_user_alias(db: AsyncSession, user_id: UUID, tag_id: UUID, alias_id: UUID) -> None:
    alias = await db.scalar(
        select(UserTagAlias).where(
            UserTagAlias.id == alias_id,
            UserTagAlias.user_id == user_id,
            UserTagAlias.tag_id == tag_id,
        )
    )
    if alias is None:
        raise TagCatalogueError("Alias not found.", 404)
    await db.delete(alias)
    await db.flush()


async def set_suppression(db: AsyncSession, user_id: UUID, raw: str, suppressed: bool) -> None:
    key = fold_key(clean_label(raw))
    current = await db.scalar(
        select(UserTagSuppression).where(
            UserTagSuppression.user_id == user_id, UserTagSuppression.tag_key == key
        )
    )
    if suppressed and current is None:
        db.add(UserTagSuppression(user_id=user_id, tag_key=key))
    elif not suppressed and current is not None:
        await db.delete(current)
    await db.flush()


# --------------------------------------------------------------- deployment defaults ---


async def get_default_tag(db: AsyncSession, tag_id: UUID) -> DefaultTag:
    tag = await db.get(DefaultTag, tag_id)
    if tag is None or tag.archived_at is not None:
        raise TagCatalogueError("Default tag not found.", 404)
    return tag


async def create_default_tag(db: AsyncSession, label: str) -> DefaultTag:
    label = clean_label(label)
    key = fold_key(label)
    existing = await db.scalar(select(DefaultTag).where(DefaultTag.key == key))
    if existing is not None:
        if existing.archived_at is not None:
            existing.archived_at = None
            existing.label = label
        return existing
    if await db.scalar(select(DefaultTagAlias.id).where(DefaultTagAlias.alias_key == key)):
        raise TagCatalogueError("That spelling is already an alias; remove the alias first.", 409)
    tag = DefaultTag(label=label, key=key)
    db.add(tag)
    await db.flush()
    return tag


async def rename_default_tag(db: AsyncSession, tag_id: UUID, label: str) -> DefaultTag:
    tag = await get_default_tag(db, tag_id)
    tag.label = clean_label(label)
    await db.flush()
    return tag


async def archive_default_tag(db: AsyncSession, tag_id: UUID) -> None:
    tag = await get_default_tag(db, tag_id)
    tag.archived_at = int(time.time())
    await db.execute(delete(DefaultTagAlias).where(DefaultTagAlias.tag_id == tag.id))
    await db.flush()


async def add_default_alias(db: AsyncSession, tag_id: UUID, raw: str) -> DefaultTagAlias:
    tag = await get_default_tag(db, tag_id)
    label = clean_label(raw)
    key = fold_key(label)
    if key == tag.key:
        raise TagCatalogueError("That spelling already names this tag.")
    if await db.scalar(
        select(DefaultTag.id).where(DefaultTag.key == key, DefaultTag.archived_at.is_(None))
    ):
        raise TagCatalogueError(
            "That spelling is a default tag of its own; archive it before aliasing it.", 409
        )
    existing = await db.scalar(select(DefaultTagAlias).where(DefaultTagAlias.alias_key == key))
    if existing is not None:
        if existing.tag_id != tag.id:
            raise TagCatalogueError("That spelling is already an alias of another tag.", 409)
        return existing
    alias = DefaultTagAlias(tag_id=tag.id, alias_key=key, alias_label=label)
    db.add(alias)
    await db.flush()
    return alias


async def remove_default_alias(db: AsyncSession, tag_id: UUID, alias_id: UUID) -> None:
    alias = await db.scalar(
        select(DefaultTagAlias).where(
            DefaultTagAlias.id == alias_id, DefaultTagAlias.tag_id == tag_id
        )
    )
    if alias is None:
        raise TagCatalogueError("Alias not found.", 404)
    await db.delete(alias)
    await db.flush()


# ----------------------------------------------------- raw tags: counts and suggestions ---


def _tag_selects(user_id: UUID) -> list[Any]:
    """One `unnest` select per taggable feature, restricted to the user's own records."""
    owned = select(Game.id).where(Game.user_id == user_id)
    return [
        select(func.unnest(Game.tags).label("tag")).where(Game.user_id == user_id),
        select(func.unnest(Anime.tags).label("tag")).where(Anime.user_id == user_id),
        select(func.unnest(TVShow.tags).label("tag")).where(TVShow.user_id == user_id),
        select(func.unnest(Movie.tags).label("tag")).where(Movie.user_id == user_id),
        select(func.unnest(MediaItem.tags).label("tag")).where(MediaItem.game_id.in_(owned)),
        select(func.unnest(GameArchive.tags).label("tag")).where(GameArchive.game_id.in_(owned)),
        select(func.unnest(GameFileItem.tags).label("tag")).where(GameFileItem.game_id.in_(owned)),
        select(func.unnest(GameNoteDetail.tags).label("tag")).where(
            GameNoteDetail.game_id.in_(owned)
        ),
    ]


async def raw_tag_counts(db: AsyncSession, user_id: UUID) -> dict[str, int]:
    """Distinct raw spellings the user's records carry, with how many times each appears."""
    counts: dict[str, int] = {}
    for stmt in _tag_selects(user_id):
        sub = stmt.subquery()
        for tag, count in (
            await db.execute(select(sub.c.tag, sql_count()).group_by(sub.c.tag))
        ).all():
            counts[tag] = counts.get(tag, 0) + int(count)
    return counts


async def discover_source_tags(db: AsyncSession, user_id: UUID) -> int:
    """Add a `source` catalogue row for each raw spelling not already covered. Returns how many
    were created; stored tags are not touched."""
    resolver = await load_resolver(db, user_id)
    created = 0
    for raw in sorted(await raw_tag_counts(db, user_id)):
        label = " ".join(raw.split())
        if not label or len(label) > TAG_LABEL_LENGTH or resolver.resolve(label) is not None:
            continue
        key = fold_key(label)
        if key in resolver.suppressed or key in resolver.user_tags:
            continue
        try:
            await create_user_tag(db, user_id, label, kind=TagKind.SOURCE)
        except TagCatalogueError:
            continue
        resolver.user_tags[key] = Resolved(label, SCOPE_USER, UUID(int=0))
        created += 1
    return created


async def alias_suggestions(db: AsyncSession, user_id: UUID) -> list[dict[str, Any]]:
    """Pairs of raw spellings that differ only slightly. Suggestions only; nothing is applied."""
    resolver = await load_resolver(db, user_id)
    counts = await raw_tag_counts(db, user_id)
    spellings = sorted(
        {fold_key(tag): tag for tag in counts if resolver.resolve(tag) is None}.items()
    )
    found: list[dict[str, Any]] = []
    for index, (key_a, raw_a) in enumerate(spellings):
        for key_b, raw_b in spellings[index + 1 :]:
            if abs(len(key_a) - len(key_b)) > 3:
                continue
            if SequenceMatcher(None, key_a, key_b).ratio() >= SUGGESTION_SIMILARITY:
                keep, fold = (raw_a, raw_b) if counts[raw_a] >= counts[raw_b] else (raw_b, raw_a)
                found.append({"tag": keep, "alias": fold, "uses": [counts[keep], counts[fold]]})
    return found[:MAX_SUGGESTIONS]


# --------------------------------------------------------------------- export / import ---

EXPORT_VERSION = 1


async def export_catalogue(db: AsyncSession, user_id: UUID) -> dict[str, Any] | None:
    """The user's private catalogue for a library export; None when there is nothing to carry.
    Deployment defaults belong to the deployment backup, not a personal export."""
    resolver_tags = (
        await db.scalars(
            select(UserTag)
            .where(UserTag.user_id == user_id, UserTag.archived_at.is_(None))
            .order_by(UserTag.label)
        )
    ).all()
    aliases = (await db.scalars(select(UserTagAlias).where(UserTagAlias.user_id == user_id))).all()
    suppressed = sorted(
        (
            await db.scalars(
                select(UserTagSuppression.tag_key).where(UserTagSuppression.user_id == user_id)
            )
        ).all()
    )
    if not resolver_tags and not suppressed:
        return None
    return {
        "version": EXPORT_VERSION,
        "tags": [
            {
                "label": tag.label,
                "key": tag.key,
                "kind": tag.kind,
                "namespace": tag.namespace,
                "aliases": sorted(a.alias_label for a in aliases if a.tag_id == tag.id),
            }
            for tag in resolver_tags
        ],
        "suppressed": suppressed,
    }


@dataclass
class RestoredCatalogue:
    tags: int = 0
    errors: list[str] = field(default_factory=list)


async def restore_catalogue(
    db: AsyncSession, user_id: UUID, section: dict[str, Any] | None
) -> RestoredCatalogue:
    """Add an exported catalogue to the importer's own. Existing tags and aliases are kept as they
    are; an entry that conflicts is reported and skipped, never overwritten."""
    restored = RestoredCatalogue()
    if not section:
        return restored
    if section.get("version") != EXPORT_VERSION:
        restored.errors.append("Tag catalogue: unsupported export version; skipped.")
        return restored
    for entry in section.get("tags") or []:
        try:
            tag = await create_user_tag(
                db,
                user_id,
                str(entry.get("label", "")),
                kind=TagKind.SOURCE if entry.get("kind") == TagKind.SOURCE else TagKind.USER,
                namespace=entry.get("namespace"),
            )
            restored.tags += 1
            for alias in entry.get("aliases") or []:
                await add_user_alias(db, user_id, tag.id, str(alias))
        except (TagCatalogueError, AttributeError, TypeError) as exc:
            restored.errors.append(f"Tag catalogue: {entry!r:.60} skipped ({exc}).")
    for key in section.get("suppressed") or []:
        try:
            await set_suppression(db, user_id, str(key), True)
        except TagCatalogueError as exc:
            restored.errors.append(f"Tag catalogue: suppression {key!r:.40} skipped ({exc}).")
    await db.commit()
    return restored
