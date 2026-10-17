"""Library layout, umbrella overview and the manage-identity view (#101).

Everything the UI shows about "one logical game" is produced here from the
identity model, so the library, the game page and the identity dialog agree:

- `library_layout` says which games share one library entry, which stay on their
  own and how related (non-merged) games are presented.
- `game_overview` returns the reconciled aggregate next to each version's own
  values. Aggregates come from `counters.reconcile` and the manual-tracking
  service; a game with no source observations (older Playnite or manual
  records) falls back to the values on its own record, never to zero.
- `identity_sources` explains the identity: linked source observations,
  relationships, decisions (including rejections) and metric counters.
"""

# pylint: disable=too-many-locals

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.preferences import load_preferences
from src.database.models.game import Game, GameStatus
from src.database.models.game_identity import (
    GameIdentity,
    GameIdentityDecision,
    GameRelationship,
    GameSourceConnection,
    GameSourceObservation,
    GameSourceObservationLink,
    RelationshipKind,
)
from src.features.game_identity import counters, graph, manual

_SAME_GAME = RelationshipKind.SAME_GAME.value
_PLAYTIME = "playtime"
_HISTORY_LIMIT = 50


async def _alive_games(db: AsyncSession, user_id: UUID) -> dict[UUID, Game]:
    rows = await db.scalars(select(Game).where(Game.user_id == user_id, Game.deleted_at.is_(None)))
    return {game.id: game for game in rows}


def _representative(group: set[UUID], root: UUID, alive: dict[UUID, Game]) -> UUID | None:
    """The merge root, or (when it is in Trash) the first live member by title."""
    if root in alive:
        return root
    live = sorted((alive[g] for g in group if g in alive), key=lambda g: (g.sort_title, str(g.id)))
    return live[0].id if live else None


async def library_layout(db: AsyncSession, user_id: UUID) -> dict[str, Any]:
    """How the library groups games: umbrella groups, related games and the user's rules."""
    preferences = await load_preferences(db, user_id)
    mode = preferences["library_version_display"]
    rules: dict[str, str] = preferences["library_relationship_display"]
    alive = await _alive_games(db, user_id)
    merge = await graph.merge_graph(db, user_id)
    separate_ids = set(
        await db.scalars(
            select(GameIdentity.game_id).where(
                GameIdentity.user_id == user_id, GameIdentity.presentation == "separate"
            )
        )
    )
    groups: dict[UUID, set[UUID]] = {}
    for child in merge.parent:
        if child in alive:
            groups.setdefault(merge.root(child), {merge.root(child)}).add(child)
    result = []
    for root, group in groups.items():
        head = _representative(group, root, alive)
        if head is None:
            continue
        members = []
        for game_id in sorted(g for g in group if g in alive and g != head):
            edge = merge.edge.get(game_id)
            display = edge.display if edge is not None else "default"
            if game_id in separate_ids or display == "link":
                shown = "separate"
            elif display == "hidden":
                shown = "hidden"
            else:
                shown = "grouped"
            members.append(
                {
                    "game_id": game_id,
                    "display": shown,
                    "relationship_id": edge.id if edge is not None else None,
                }
            )
        if members:
            result.append({"umbrella_id": head, "members": members})
    related = []
    edges = await db.scalars(
        select(GameRelationship).where(
            GameRelationship.user_id == user_id,
            GameRelationship.ended_at.is_(None),
            GameRelationship.kind != _SAME_GAME,
            GameRelationship.from_game_id.is_not(None),
            GameRelationship.to_game_id.is_not(None),
        )
    )
    for edge in edges:
        if edge.from_game_id not in alive or edge.to_game_id not in alive:
            continue
        shown = edge.display if edge.display != "default" else rules.get(edge.kind, "link")
        related.append(
            {
                "id": edge.id,
                "kind": edge.kind,
                "label": edge.label,
                "from_id": edge.from_game_id,
                "to_id": edge.to_game_id,
                "display": shown,
            }
        )
    related.sort(key=lambda r: str(r["id"]))
    result.sort(key=lambda g: str(g["umbrella_id"]))
    return {"mode": mode, "rules": rules, "groups": result, "related": related}


def _playtime_by_game(metric: dict[str, Any]) -> dict[UUID, int]:
    found: dict[UUID, int] = {}
    for view in metric["counters"]:
        if view["effective"] is not None:
            found[view["game_id"]] = found.get(view["game_id"], 0) + int(view["effective"])
    return found


def _freshness(
    observations: list[tuple[GameSourceObservation, GameSourceConnection]],
) -> dict[str, Any]:
    seen = [o.last_observed_at for o, _ in observations if o.last_observed_at]
    return {
        "sources": sorted({c.source for _, c in observations}),
        "last_observed_at": max(seen) if seen else None,
    }


async def _observations_by_game(
    db: AsyncSession, user_id: UUID, games: set[UUID]
) -> dict[UUID, list[tuple[GameSourceObservation, GameSourceConnection]]]:
    rows = await db.execute(
        select(GameSourceObservationLink.game_id, GameSourceObservation, GameSourceConnection)
        .join(
            GameSourceObservation,
            GameSourceObservation.id == GameSourceObservationLink.observation_id,
        )
        .join(GameSourceConnection, GameSourceConnection.id == GameSourceObservation.connection_id)
        .where(
            GameSourceObservationLink.user_id == user_id,
            GameSourceObservationLink.unlinked_at.is_(None),
            GameSourceObservationLink.game_id.in_(games),
        )
        .order_by(GameSourceConnection.source, GameSourceObservation.external_id)
    )
    found: dict[UUID, list[tuple[GameSourceObservation, GameSourceConnection]]] = {}
    for game_id, observation, connection in rows.tuples():
        found.setdefault(game_id, []).append((observation, connection))
    return found


async def game_overview(db: AsyncSession, user_id: UUID, game_id: UUID) -> dict[str, Any]:
    """The reconciled overall values and each version's own values for one logical game."""
    metrics = await counters.reconcile(db, user_id, game_id)
    tracking = await manual.tracking_view(db, user_id, game_id)
    alive = await _alive_games(db, user_id)
    members = [alive[g] for g in metrics["games"] if g in alive]
    root = metrics["identity_id"]
    members.sort(key=lambda g: (g.id != root, g.sort_title, str(g.id)))
    ids = {g.id for g in members}
    observed = await _observations_by_game(db, user_id, ids)
    placements = {
        row.game_id: row
        for row in await db.scalars(
            select(GameIdentity).where(
                GameIdentity.user_id == user_id, GameIdentity.game_id.in_(ids)
            )
        )
    }
    merge = await graph.merge_graph(db, user_id)
    achievement_rows = {v["game_id"]: v for v in tracking["achievements"]["versions"]}
    completion_rows = {v["game_id"]: v for v in tracking["completion"]["versions"]}
    playtime = metrics["metrics"][_PLAYTIME]
    reconciled = _playtime_by_game(playtime)

    versions = []
    total_seconds = 0
    for game in members:
        has_counter = game.id in reconciled
        seconds = reconciled[game.id] if has_counter else game.playtime_seconds
        total_seconds += 0 if has_counter else game.playtime_seconds
        edge = merge.edge.get(game.id)
        placement = placements.get(game.id)
        achievements = achievement_rows.get(game.id)
        completion = completion_rows.get(game.id)
        versions.append(
            {
                "game_id": game.id,
                "title": game.title,
                "label": placement.label if placement and placement.label else None,
                "edition": placement.edition if placement else None,
                "platform": game.platform,
                "source": game.source,
                "status": game.status.value,
                "is_primary": game.id == root,
                "display": edge.display if edge is not None else "default",
                "relationship_id": edge.id if edge is not None else None,
                "owned": game.status != GameStatus.WISHLIST,
                "playtime_seconds": seconds,
                "playtime_basis": "reconciled" if has_counter else "record",
                "last_played_at": game.last_played_at,
                "achievements": {
                    "unlocked": achievements["unlocked"] if achievements else 0,
                    "total": achievements["total"] if achievements else 0,
                    "manual_total": achievements["manual"]["total"] if achievements else 0,
                },
                "completed": completion["completed"] if completion else False,
                "completion_user_set": completion["user_set"] if completion else False,
                **_freshness(observed.get(game.id, [])),
            }
        )
    total_seconds += playtime["effective"] or 0
    last_played = [
        v
        for v in (
            metrics["metrics"]["last_played"]["effective"],
            *(g.last_played_at for g in members),
        )
        if v
    ]
    owned = [v for v in versions if v["owned"]]
    platforms = sorted({v["platform"] for v in owned if v["platform"]})
    version_sum = sum(v["playtime_seconds"] for v in versions)
    return {
        "game_id": game_id,
        "umbrella_id": root,
        "aggregate": {
            "playtime_seconds": total_seconds,
            "playtime_is_sum_of_versions": total_seconds == version_sum,
            "last_played_at": max(last_played) if last_played else None,
            "version_count": len(versions),
            "owned_count": len(owned),
            "owned_platforms": platforms,
            "completion": {
                "summary": tracking["completion"]["summary"],
                "completed": tracking["completion"]["completed"],
                "of": tracking["completion"]["of"],
                "partial": tracking["completion"]["partial"],
                "all_completed": tracking["completion"]["all_completed"],
            },
            "achievements": tracking["achievements"]["umbrella"],
        },
        "versions": versions,
        "rating": metrics["rating"],
    }


def _decision_out(row: GameIdentityDecision) -> dict[str, Any]:
    return {
        "id": row.id,
        "subject_kind": row.subject_kind,
        "first_game_id": row.first_game_id,
        "second_game_id": row.second_game_id,
        "observation_id": row.observation_id,
        "outcome": row.outcome,
        "actor_kind": row.actor_kind,
        "actor_detail": row.actor_detail,
        "reason": row.reason,
        "rule_version": row.rule_version,
        "confidence": None if row.confidence is None else float(row.confidence),
        "evidence": row.evidence,
        "created_at": row.created_at,
        "superseded_at": row.superseded_at,
    }


def _observation_out(
    observation: GameSourceObservation, connection: GameSourceConnection, game_id: UUID
) -> dict[str, Any]:
    facts = observation.observed_facts or {}
    return {
        "observation_id": observation.id,
        "game_id": game_id,
        "source": connection.source,
        "source_kind": connection.source_kind,
        "scope": connection.source_scope,
        "connection_label": connection.label,
        "disconnected": connection.disconnected_at is not None,
        "external_id": observation.external_id,
        "title": observation.observed_title,
        "platform": observation.observed_platform,
        "edition": observation.observed_edition,
        "availability": observation.availability,
        "unavailable_since": observation.unavailable_since,
        "first_observed_at": observation.first_observed_at,
        "last_observed_at": observation.last_observed_at,
        "revision": observation.revision,
        "facts": {
            key: facts[key]
            for key in (
                "playtime_seconds",
                "last_played_at",
                "status",
                "owned",
                "installed",
                "achievements_unlocked",
                "achievements_total",
            )
            if key in facts
        },
        "field_observed_at": observation.field_observed_at or {},
    }


async def identity_sources(db: AsyncSession, user_id: UUID, game_id: UUID) -> dict[str, Any]:
    """Why this group is one game: sources, relationships, decisions and counters."""
    await graph.require_games(db, user_id, game_id)
    merge = await graph.merge_graph(db, user_id)
    group = merge.group(game_id)
    alive = await _alive_games(db, user_id)
    members = sorted(
        (alive[g] for g in group if g in alive),
        key=lambda g: (g.id != merge.root(game_id), g.sort_title, str(g.id)),
    )
    ids = {g.id for g in members}
    observed = await _observations_by_game(db, user_id, ids)
    observations = [
        _observation_out(o, c, gid)
        for gid in (g.id for g in members)
        for o, c in observed.get(gid, [])
    ]
    edges = list(
        await db.scalars(
            select(GameRelationship)
            .where(
                GameRelationship.user_id == user_id,
                or_(GameRelationship.from_game_id.in_(ids), GameRelationship.to_game_id.in_(ids)),
            )
            .order_by(GameRelationship.ended_at.is_not(None), GameRelationship.created_at.desc())
            .limit(_HISTORY_LIMIT)
        )
    )
    titles = {
        g.id: g.title
        for g in await db.scalars(
            select(Game).where(
                Game.user_id == user_id,
                Game.id.in_(
                    {e.from_game_id for e in edges if e.from_game_id}
                    | {e.to_game_id for e in edges if e.to_game_id}
                    | ids
                ),
            )
        )
    }
    obs_ids = {o["observation_id"] for o in observations}
    scope = or_(
        GameIdentityDecision.first_game_id.in_(ids),
        GameIdentityDecision.second_game_id.in_(ids),
        GameIdentityDecision.observation_id.in_(obs_ids),
    )
    decisions = list(
        await db.scalars(
            select(GameIdentityDecision)
            .where(GameIdentityDecision.user_id == user_id, scope)
            .order_by(GameIdentityDecision.created_at.desc(), GameIdentityDecision.id)
            .limit(_HISTORY_LIMIT)
        )
    )
    for row in decisions:
        for other in (row.first_game_id, row.second_game_id):
            if other and other not in titles:
                game = await db.get(Game, other)
                if game is not None and game.user_id == user_id:
                    titles[other] = game.title
    reconciled = await counters.reconcile(db, user_id, game_id)
    return {
        "game_id": game_id,
        "umbrella_id": merge.root(game_id),
        "titles": {str(k): v for k, v in titles.items()},
        "versions": [
            {
                "game_id": g.id,
                "title": g.title,
                "platform": g.platform,
                "source": g.source,
                "playnite_guid": g.playnite_guid,
            }
            for g in members
        ],
        "observations": observations,
        "relationships": [
            {
                "id": e.id,
                "kind": e.kind,
                "label": e.label,
                "from_id": e.from_game_id,
                "to_id": e.to_game_id,
                "display": e.display,
                "origin": e.origin,
                "created_at": e.created_at,
                "ended_at": e.ended_at,
            }
            for e in edges
        ],
        "decisions": [_decision_out(d) for d in decisions],
        "counters": {
            name: metric["counters"]
            for name, metric in reconciled["metrics"].items()
            if "counters" in metric
        },
    }


async def set_relationship_display(
    db: AsyncSession, user_id: UUID, relationship_id: UUID, display: str
) -> GameRelationship:
    """Change how an edge is presented. History is kept: only the display changes."""
    await graph.lock_user_identities(db, user_id)
    edge = await db.scalar(
        select(GameRelationship)
        .where(GameRelationship.id == relationship_id, GameRelationship.user_id == user_id)
        .with_for_update()
    )
    if edge is None:
        raise LookupError("Relationship not found")
    if edge.ended_at is not None:
        raise graph.IdentityInvalid("This relationship has ended")
    edge.display = display
    edge.updated_at = graph.now_seconds()
    await db.flush()
    return edge


async def change_relationship(
    db: AsyncSession,
    user_id: UUID,
    relationship_id: UUID,
    *,
    kind: str,
    label: str = "",
    display: str | None = None,
) -> GameRelationship:
    """Change an edge's type: the old edge is ended and a new one created, so the
    history shows what it used to be. A merge edge is changed by unmerging."""
    await graph.lock_user_identities(db, user_id)
    edge = await db.scalar(
        select(GameRelationship)
        .where(GameRelationship.id == relationship_id, GameRelationship.user_id == user_id)
        .with_for_update()
    )
    if edge is None:
        raise LookupError("Relationship not found")
    if edge.kind == _SAME_GAME:
        raise graph.IdentityInvalid("Unmerge to change a merge")
    if edge.ended_at is not None or edge.from_game_id is None or edge.to_game_id is None:
        raise graph.IdentityInvalid("This relationship has ended")
    if (edge.kind, edge.label) == (kind, label.strip()):
        if display is not None:
            edge.display = display
            await db.flush()
        return edge
    new_display = edge.display if display is None else display
    await graph.end_relationship(db, user_id, relationship_id)
    return await graph.add_relationship(
        db, user_id, kind, edge.from_game_id, edge.to_game_id, label=label, display=new_display
    )
