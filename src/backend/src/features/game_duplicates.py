"""Conservative duplicate suggestions across host and plugin library imports."""

from collections import defaultdict
from collections.abc import Iterable
from itertools import combinations
from typing import Any
from unicodedata import normalize
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models.game import Game
from src.database.models.game_duplicate_dismissal import GameDuplicateDismissal

_SUMMARY_FIELDS = (
    "id",
    "title",
    "source",
    "platform",
    "release_date",
    "external_id",
    "provider_ids",
    "status",
    "playtime_seconds",
    "locked_fields",
    "parent_game_id",
    "relationship_type",
)


def match_key(title: str) -> str:
    """Ignore cosmetic punctuation without stripping accents, scripts or editions."""
    title = normalize("NFKC", title.replace("™", "").replace("®", "").replace("©", ""))
    title = title.casefold().replace("'", "").replace("’", "")
    return " ".join("".join(char if char.isalnum() else " " for char in title).split())


def compatible(first: dict[str, Any], second: dict[str, Any]) -> bool:
    """Conflicting known years, editions or identities must not be title suggestions."""
    for field in ("platform", "relationship_type"):
        if first[field] and second[field] and first[field] != second[field]:
            return False
    if first["parent_game_id"] or second["parent_game_id"]:
        return False
    a_year, b_year = first["release_date"], second["release_date"]
    if a_year and b_year and a_year.year != b_year.year:
        return False
    a_ids, b_ids = first["provider_ids"] or {}, second["provider_ids"] or {}
    if any(a_ids[key] != b_ids[key] for key in a_ids.keys() & b_ids.keys()):
        return False
    return not (
        first["source"]
        and first["source"] == second["source"]
        and first["external_id"]
        and second["external_id"]
        and first["external_id"] != second["external_id"]
    )


def _evidence_groups(
    games: Iterable[RowMapping],
) -> dict[tuple[str, ...], list[dict[str, Any]]]:
    """Group compact rows by title or explicit provider identity, keeping manual titles."""
    groups: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in games:
        game = dict(row)
        key = match_key(row["title"])
        if key:
            groups[("same_title", key)].append(game)
        for namespace, identity in (row["provider_ids"] or {}).items():
            if identity:
                groups[("shared_identity", namespace, identity)].append(game)
    return groups


async def suggestions(db: AsyncSession, user_id: UUID, limit: int = 50) -> dict[str, Any]:
    """Read compact owned rows once; bound the response and avoid provider requests."""
    games = (
        await db.execute(
            select(*(getattr(Game, field) for field in _SUMMARY_FIELDS))
            .where(Game.user_id == user_id, Game.deleted_at.is_(None))
            .order_by(Game.id)
        )
    ).mappings()
    dismissed = set(
        (
            await db.execute(
                select(
                    GameDuplicateDismissal.first_game_id, GameDuplicateDismissal.second_game_id
                ).where(GameDuplicateDismissal.user_id == user_id)
            )
        ).all()
    )
    pairs: list[dict[str, Any]] = []
    seen: set[tuple[UUID, UUID]] = set()
    for evidence, group in _evidence_groups(games).items():
        for first, second in combinations(group, 2):
            pair = (first["id"], second["id"])
            if pair in seen or pair in dismissed or not compatible(first, second):
                continue
            seen.add(pair)
            if len(pairs) == limit:
                return {"pairs": pairs, "has_more": True}
            pairs.append({"first": first, "second": second, "reason": evidence[0]})
    return {"pairs": pairs, "has_more": False}


async def keep_both(db: AsyncSession, user_id: UUID, first_id: UUID, second_id: UUID) -> None:
    """Only explicit review persists a decision; imports never decide for the user."""
    ids = sorted((first_id, second_id))
    owned = set(
        await db.scalars(
            select(Game.id)
            .where(Game.id.in_(ids), Game.user_id == user_id, Game.deleted_at.is_(None))
            .order_by(Game.id)
            .with_for_update()
        )
    )
    if len(owned) != 2:
        raise LookupError("Both games must be active entries in your library.")
    await db.execute(
        insert(GameDuplicateDismissal)
        .values(user_id=user_id, first_game_id=ids[0], second_game_id=ids[1])
        .on_conflict_do_nothing()
    )
    await db.commit()
