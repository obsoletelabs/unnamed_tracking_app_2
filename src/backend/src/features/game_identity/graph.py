"""Relationship graph rules shared by merges and user-defined relationships."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, NamedTuple
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models.game import Game
from src.database.models.game_identity import GameRelationship, RelationshipKind

# Kinds where "A is a X of B" cannot also hold in the other direction through any
# chain. Everything else (`related_to`, `custom`, memberships) may be circular,
# because users are allowed to build unusual groupings.
ACYCLIC_KINDS = frozenset(
    {
        RelationshipKind.SAME_GAME.value,
        RelationshipKind.VERSION_OF.value,
        RelationshipKind.REMAKE_OF.value,
        RelationshipKind.REMASTER_OF.value,
        RelationshipKind.PORT_OF.value,
        RelationshipKind.DLC_OF.value,
        RelationshipKind.EXPANSION_OF.value,
        RelationshipKind.STANDALONE_EXPANSION_OF.value,
        RelationshipKind.MOD_OF.value,
        RelationshipKind.MODPACK_OF.value,
        RelationshipKind.TOTAL_CONVERSION_OF.value,
    }
)
_MAX_DEPTH = 1000


class IdentityError(Exception):
    """Base class for identity rule violations."""


class IdentityInvalid(IdentityError):
    """The request is malformed or breaks a structural rule (self edge, cycle)."""


class IdentityConflict(IdentityError):
    """The request conflicts with an existing decision or relationship."""


def now_seconds() -> int:
    return int(time.time())


async def lock_user_identities(db: AsyncSession, user_id: UUID) -> None:
    """Serialise identity changes of one user until the transaction ends.

    Sync, user edits and automatic matching all take this lock, so a group is
    never merged, split and re-merged concurrently."""
    await db.execute(
        select(func.pg_advisory_xact_lock(func.hashtextextended(f"gid:{user_id}", 95)))
    )


async def require_games(db: AsyncSession, user_id: UUID, *game_ids: UUID) -> None:
    """Every game must belong to this user; another user's game is 'not found'."""
    wanted = set(game_ids)
    found = set(
        await db.scalars(select(Game.id).where(Game.id.in_(wanted), Game.user_id == user_id))
    )
    if found != wanted:
        raise LookupError("Game not found")


async def active_edges(
    db: AsyncSession, user_id: UUID, kind: str, label: str | None = None
) -> list[GameRelationship]:
    statement = select(GameRelationship).where(
        GameRelationship.user_id == user_id,
        GameRelationship.kind == kind,
        GameRelationship.ended_at.is_(None),
        GameRelationship.from_game_id.is_not(None),
        GameRelationship.to_game_id.is_not(None),
    )
    if label is not None:
        statement = statement.where(GameRelationship.label == label)
    return list(await db.scalars(statement))


class EdgeView(NamedTuple):
    """The read-only part of a same-game edge that the views show."""

    id: UUID
    display: str


@dataclass(frozen=True)
class MergeGraph:
    """Active `same_game` edges of one user: each identity absorbs into one parent."""

    parent: dict[UUID, UUID]
    edge: dict[UUID, Any]  # GameRelationship, or EdgeView for a read-only graph

    def root(self, game_id: UUID) -> UUID:
        seen = {game_id}
        while game_id in self.parent:
            game_id = self.parent[game_id]
            if game_id in seen:  # impossible by construction; never loop forever
                raise IdentityInvalid("Merge cycle detected")
            seen.add(game_id)
        return game_id

    def children(self, game_id: UUID) -> list[UUID]:
        return [child for child, parent in self.parent.items() if parent == game_id]

    def subtree(self, game_id: UUID) -> set[UUID]:
        """The identity and everything that is (transitively) merged into it."""
        found, frontier = {game_id}, [game_id]
        while frontier:
            nxt = [c for node in frontier for c in self.children(node) if c not in found]
            found.update(nxt)
            frontier = nxt
        return found

    def group(self, game_id: UUID) -> set[UUID]:
        return self.subtree(self.root(game_id))


async def merge_graph(db: AsyncSession, user_id: UUID, *, readonly: bool = False) -> MergeGraph:
    """The user's active merge edges.

    `readonly` reads three columns per edge instead of building an ORM object for each one,
    which is what a view that only follows merges needs. Anything that changes an edge takes
    the default, which returns the entities themselves."""
    if readonly:
        rows = await db.execute(
            select(
                GameRelationship.id,
                GameRelationship.from_game_id,
                GameRelationship.to_game_id,
                GameRelationship.display,
            ).where(
                GameRelationship.user_id == user_id,
                GameRelationship.kind == RelationshipKind.SAME_GAME.value,
                GameRelationship.ended_at.is_(None),
                GameRelationship.from_game_id.is_not(None),
                GameRelationship.to_game_id.is_not(None),
            )
        )
        found = rows.all()
        return MergeGraph(
            parent={child: parent for _, child, parent, _ in found},
            edge={child: EdgeView(edge_id, shown) for edge_id, child, _, shown in found},
        )
    edges = await active_edges(db, user_id, RelationshipKind.SAME_GAME.value)
    return MergeGraph(
        parent={e.from_game_id: e.to_game_id for e in edges if e.from_game_id and e.to_game_id},
        edge={e.from_game_id: e for e in edges if e.from_game_id},
    )


async def resolve_identity(db: AsyncSession, user_id: UUID, game_id: UUID) -> UUID:
    """The identity a game is shown as: itself unless merged into another."""
    await require_games(db, user_id, game_id)
    return (await merge_graph(db, user_id, readonly=True)).root(game_id)


async def would_cycle(
    db: AsyncSession, user_id: UUID, kind: str, label: str, from_id: UUID, to_id: UUID
) -> bool:
    """True when adding from -> to makes `to` reach `from` through the same kind."""
    if kind not in ACYCLIC_KINDS:
        return False
    outgoing: dict[UUID, list[UUID]] = {}
    for edge in await active_edges(db, user_id, kind, label):
        if edge.from_game_id and edge.to_game_id:
            outgoing.setdefault(edge.from_game_id, []).append(edge.to_game_id)
    seen, frontier = {to_id}, [to_id]
    for _ in range(_MAX_DEPTH):
        if not frontier:
            return False
        if from_id in frontier:
            return True
        frontier = [n for node in frontier for n in outgoing.get(node, []) if n not in seen]
        seen.update(frontier)
    return True


async def add_relationship(
    db: AsyncSession,
    user_id: UUID,
    kind: str,
    from_id: UUID,
    to_id: UUID,
    *,
    label: str = "",
    display: str = "default",
    origin: str = "user",
    decision_id: UUID | None = None,
    now: int | None = None,
) -> GameRelationship:
    """Create a typed edge. `same_game` is created by a merge, not here."""
    if kind == RelationshipKind.SAME_GAME.value:
        raise IdentityInvalid("Use a merge to mark identities as the same game")
    if kind not in {k.value for k in RelationshipKind}:
        raise IdentityInvalid(f"Unknown relationship kind {kind!r}")
    label = label.strip()
    if (kind == RelationshipKind.CUSTOM.value) != bool(label):
        raise IdentityInvalid("Only custom relationships carry (and require) a label")
    if from_id == to_id:
        raise IdentityInvalid("A game cannot be related to itself")
    await lock_user_identities(db, user_id)
    await require_games(db, user_id, from_id, to_id)
    for edge in await active_edges(db, user_id, kind, label):
        if (edge.from_game_id, edge.to_game_id) == (from_id, to_id):
            return edge
    if await would_cycle(db, user_id, kind, label, from_id, to_id):
        raise IdentityInvalid(f"A {kind} relationship cannot be circular")
    stamp = now_seconds() if now is None else now
    relationship = GameRelationship(
        user_id=user_id,
        kind=kind,
        label=label,
        from_game_id=from_id,
        to_game_id=to_id,
        display=display,
        origin=origin,
        decision_id=decision_id,
        created_at=stamp,
        updated_at=stamp,
    )
    db.add(relationship)
    await db.flush()
    return relationship


async def end_relationship(
    db: AsyncSession, user_id: UUID, relationship_id: UUID, *, now: int | None = None
) -> GameRelationship:
    """End (never delete) an edge. A merge edge is ended by `unmerge`."""
    await lock_user_identities(db, user_id)
    relationship = await db.scalar(
        select(GameRelationship)
        .where(GameRelationship.id == relationship_id, GameRelationship.user_id == user_id)
        .with_for_update()
    )
    if relationship is None:
        raise LookupError("Relationship not found")
    if relationship.kind == RelationshipKind.SAME_GAME.value:
        raise IdentityInvalid("End a merge with unmerge so the decision is recorded")
    if relationship.ended_at is None:
        stamp = now_seconds() if now is None else now
        relationship.ended_at = max(stamp, relationship.created_at)
        await db.flush()
    return relationship
