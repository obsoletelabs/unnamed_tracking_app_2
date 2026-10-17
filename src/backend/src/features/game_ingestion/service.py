"""Idempotent persistence of source observations.

The service stores what a source reported and nothing else. It never creates,
merges or renames a game, never overwrites a user decision, and never removes
an observation. Attaching an observation to a tracked identity is a separate,
recorded step that ingestion performs only for a record that has never been
linked (or whose link points at a game in Trash); any other move is an explicit
`relink_observation` by a user, a migration or a reviewed automatic rule.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Collection, Mapping
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models.game import Game
from src.database.models.game_identity import (
    GameSourceConnection,
    GameSourceObservation,
    GameSourceObservationLink,
    GameSourceObservationRevision,
    ObservationAvailability,
    RecordOrigin,
)
from src.features.game_ingestion.contract import (
    ObservationBatchV1,
    SourceConnectionRef,
    SourceObservationV1,
)

Outcome = Literal["created", "updated", "unchanged", "stale"]
_SCALARS = {
    "title": "observed_title",
    "platform": "observed_platform",
    "edition": "observed_edition",
    "release_date": "observed_release_date",
}
# Origins that may move an observation. Ingestion itself is deliberately absent.
_RELINK_ORIGINS = frozenset(
    {RecordOrigin.USER.value, RecordOrigin.MIGRATION.value, RecordOrigin.AUTOMATIC.value}
)


@dataclass(frozen=True)
class ObservationResult:
    external_id: str
    observation_id: UUID
    outcome: Outcome
    revision: int
    changed: tuple[str, ...] = ()


@dataclass
class BatchResult:
    connection_id: UUID
    reconnected: bool = False
    results: list[ObservationResult] = field(default_factory=list)

    def count(self, outcome: Outcome) -> int:
        return sum(result.outcome == outcome for result in self.results)


def _now() -> int:
    return int(time.time())


def payload_digest(state: Mapping[str, Any]) -> str:
    """Stable digest of an observation's reported state."""
    return hashlib.sha256(
        json.dumps(state, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def _state(observation: GameSourceObservation) -> dict[str, Any]:
    release = observation.observed_release_date
    return {
        "title": observation.observed_title,
        "platform": observation.observed_platform,
        "edition": observation.observed_edition,
        "release_date": release.isoformat() if release else None,
        "provider_ids": observation.observed_provider_ids or {},
        "facts": observation.observed_facts or {},
        "availability": observation.availability,
    }


def _column_value(name: str, value: Any) -> Any:
    return date.fromisoformat(value) if name == "release_date" else value


async def get_or_create_connection(
    db: AsyncSession, user_id: UUID, ref: SourceConnectionRef, *, now: int | None = None
) -> tuple[GameSourceConnection, bool]:
    """The connection for this account, reconnecting it if it was disconnected.

    Returns `(connection, reconnected)`. Reconnecting reuses the same row, so
    every earlier observation and link is still attached to it."""
    stamp = _now() if now is None else now
    await db.execute(
        pg_insert(GameSourceConnection)
        .values(
            id=uuid4(),
            user_id=user_id,
            source_kind=ref.source_kind,
            source=ref.source,
            source_scope=ref.source_scope,
            label=ref.label,
            connected_at=stamp,
            created_at=stamp,
            updated_at=stamp,
        )
        .on_conflict_do_nothing(constraint="uq_game_source_connections_identity")
    )
    connection = await db.scalar(
        select(GameSourceConnection)
        .where(
            GameSourceConnection.user_id == user_id,
            GameSourceConnection.source_kind == ref.source_kind,
            GameSourceConnection.source == ref.source,
            GameSourceConnection.source_scope == ref.source_scope,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    assert connection is not None
    reconnected = connection.disconnected_at is not None
    if reconnected:
        connection.disconnected_at = None
        connection.connected_at = stamp
    if ref.label and connection.label != ref.label:
        connection.label = ref.label
    return connection, reconnected


async def find_connection(
    db: AsyncSession, user_id: UUID, ref: SourceConnectionRef, *, lock: bool = False
) -> GameSourceConnection | None:
    statement = select(GameSourceConnection).where(
        GameSourceConnection.user_id == user_id,
        GameSourceConnection.source_kind == ref.source_kind,
        GameSourceConnection.source == ref.source,
        GameSourceConnection.source_scope == ref.source_scope,
    )
    if lock:
        statement = statement.with_for_update().execution_options(populate_existing=True)
    return await db.scalar(statement)


def _initial_values(item: SourceObservationV1, stamp: int) -> dict[str, Any]:
    reported = item.reported()
    unavailable = reported.get("available") is False
    values: dict[str, Any] = {
        column: _column_value(name, reported[name])
        for name, column in _SCALARS.items()
        if name in reported
    }
    values.update(
        observed_provider_ids=reported.get("provider_ids", {}),
        observed_facts=reported.get("facts", {}),
        field_observed_at=_observed_times(reported, stamp),
        source_updated_at=reported.get("source_updated_at"),
        availability=(
            ObservationAvailability.UNAVAILABLE
            if unavailable
            else ObservationAvailability.AVAILABLE
        ).value,
        unavailable_since=stamp if unavailable else None,
        revision=1,
        first_observed_at=stamp,
        last_observed_at=stamp,
        created_at=stamp,
        updated_at=stamp,
    )
    return values


def _observed_times(reported: Mapping[str, Any], stamp: int) -> dict[str, int]:
    times = {name: stamp for name in _SCALARS if name in reported}
    if "provider_ids" in reported:
        times["provider_ids"] = stamp
    times.update({name: stamp for name in reported.get("facts", {})})
    return times


def _changes(observation: GameSourceObservation, reported: Mapping[str, Any]) -> dict[str, Any]:
    """The subset of the reported fields that differs from what is stored."""
    state = _state(observation)
    changes: dict[str, Any] = {}
    for name in _SCALARS:
        if name in reported and reported[name] != state[name]:
            changes[name] = reported[name]
    for section in ("provider_ids", "facts"):
        differing = {
            key: value
            for key, value in reported.get(section, {}).items()
            if state[section].get(key) != value
        }
        if differing:
            changes[section] = differing
    if "source_updated_at" in reported and reported["source_updated_at"] != (
        observation.source_updated_at
    ):
        changes["source_updated_at"] = reported["source_updated_at"]
    if "available" in reported:
        wanted = (
            ObservationAvailability.AVAILABLE
            if reported["available"]
            else ObservationAvailability.UNAVAILABLE
        ).value
        if observation.availability != wanted:
            changes["availability"] = wanted
    return changes


def _is_stale(observation: GameSourceObservation, reported: Mapping[str, Any]) -> bool:
    """An older delivery (a late retry) must not overwrite newer source state."""
    incoming = reported.get("source_updated_at")
    stored = observation.source_updated_at
    return incoming is not None and stored is not None and incoming < stored


def _apply(
    observation: GameSourceObservation,
    reported: Mapping[str, Any],
    changes: Mapping[str, Any],
    stamp: int,
) -> GameSourceObservationRevision:
    """Write only the changed fields, then append the revision that records them."""
    for name, column in _SCALARS.items():
        if name in changes:
            setattr(observation, column, _column_value(name, changes[name]))
    if "provider_ids" in changes:
        observation.observed_provider_ids = {
            **(observation.observed_provider_ids or {}),
            **changes["provider_ids"],
        }
    if "facts" in changes:
        observation.observed_facts = {**(observation.observed_facts or {}), **changes["facts"]}
    if "source_updated_at" in changes:
        observation.source_updated_at = changes["source_updated_at"]
    if "availability" in changes:
        observation.availability = changes["availability"]
        unavailable = changes["availability"] == ObservationAvailability.UNAVAILABLE.value
        observation.unavailable_since = stamp if unavailable else None
    observation.field_observed_at = {
        **(observation.field_observed_at or {}),
        **_observed_times(reported, stamp),
    }
    observation.revision += 1
    observation.last_observed_at = stamp
    observation.payload_digest = payload_digest(_state(observation))
    return GameSourceObservationRevision(
        user_id=observation.user_id,
        observation_id=observation.id,
        revision=observation.revision,
        payload_digest=observation.payload_digest,
        changes=dict(changes),
        observed_at=stamp,
    )


async def _locked_observations(
    db: AsyncSession, connection_id: UUID, external_ids: Collection[str]
) -> dict[str, GameSourceObservation]:
    rows = await db.scalars(
        select(GameSourceObservation)
        .where(
            GameSourceObservation.connection_id == connection_id,
            GameSourceObservation.external_id.in_(external_ids),
        )
        .order_by(GameSourceObservation.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return {row.external_id: row for row in rows}


# One batch touches connection, observations, revisions and links together.
# pylint: disable-next=too-many-locals
async def ingest_batch(
    db: AsyncSession,
    user_id: UUID,
    batch: ObservationBatchV1,
    *,
    attach: Mapping[str, UUID] | None = None,
    now: int | None = None,
) -> BatchResult:
    """Persist a batch of observations. Replaying a batch changes nothing.

    `attach` maps external IDs to the games row a compatibility adapter has
    already resolved by its own, unchanged rules; see `_attach`. The caller
    owns the transaction."""
    stamp = _now() if now is None else now
    connection, reconnected = await get_or_create_connection(
        db, user_id, batch.connection, now=stamp
    )
    items = {item.external_id: item for item in batch.observations}
    existing = await _locked_observations(db, connection.id, items)
    missing = [item for key, item in items.items() if key not in existing]
    if missing:
        inserted = await db.execute(
            pg_insert(GameSourceObservation)
            .values(
                [
                    {
                        "id": uuid4(),
                        "user_id": user_id,
                        "connection_id": connection.id,
                        "external_id": item.external_id,
                        **_initial_values(item, stamp),
                    }
                    for item in missing
                ]
            )
            .on_conflict_do_nothing(constraint="uq_game_source_observations_record")
            .returning(GameSourceObservation.external_id)
        )
        created_ids = set(inserted.scalars())
        # Another transaction delivered the same record first: treat it as existing.
        raced = [item.external_id for item in missing if item.external_id not in created_ids]
        created_observations = await _locked_observations(db, connection.id, created_ids)
        existing.update(await _locked_observations(db, connection.id, raced))
    else:
        created_ids, created_observations = set(), {}

    result = BatchResult(connection_id=connection.id, reconnected=reconnected)
    revisions: list[GameSourceObservationRevision] = []
    for key, item in items.items():
        reported = item.reported()
        if key in created_ids:
            observation = created_observations[key]
            observation.payload_digest = payload_digest(_state(observation))
            revisions.append(
                GameSourceObservationRevision(
                    user_id=user_id,
                    observation_id=observation.id,
                    revision=1,
                    payload_digest=observation.payload_digest,
                    changes=reported,
                    observed_at=stamp,
                )
            )
            result.results.append(ObservationResult(key, observation.id, "created", 1))
            continue
        observation = existing[key]
        if _is_stale(observation, reported):
            result.results.append(
                ObservationResult(key, observation.id, "stale", observation.revision)
            )
            continue
        changes = _changes(observation, reported)
        if not changes:
            result.results.append(
                ObservationResult(key, observation.id, "unchanged", observation.revision)
            )
            continue
        revisions.append(_apply(observation, reported, changes, stamp))
        result.results.append(
            ObservationResult(key, observation.id, "updated", observation.revision, tuple(changes))
        )
    db.add_all(revisions)
    if attach:
        observation_ids = {r.external_id: r.observation_id for r in result.results}
        await _attach(
            db, user_id, {observation_ids[k]: v for k, v in attach.items() if k in items}, stamp
        )
    await db.flush()
    return result


# pylint: disable-next=too-many-locals
async def _attach(
    db: AsyncSession, user_id: UUID, targets: Mapping[UUID, UUID], stamp: int
) -> None:
    """Link records that have no link history to the game the adapter resolved.

    A record that was ever linked keeps its link, so a source can never undo a
    reviewed move or a user's unlink. The one exception is a link whose game is
    in Trash while the adapter resolved a different active game: the old link
    is ended (kept as history) and the new one opened."""
    if not targets:
        return
    games = {
        row.id: row.deleted_at
        for row in await db.execute(
            select(Game.id, Game.deleted_at).where(
                Game.user_id == user_id, Game.id.in_(set(targets.values()))
            )
        )
    }
    unknown = set(targets.values()) - set(games)
    if unknown:
        raise ValueError("Cannot attach an observation to a game the user does not own")
    links = await db.execute(
        select(
            GameSourceObservationLink.id,
            GameSourceObservationLink.observation_id,
            GameSourceObservationLink.game_id,
            GameSourceObservationLink.unlinked_at,
            Game.deleted_at,
        )
        .join(
            Game,
            (Game.id == GameSourceObservationLink.game_id)
            & (Game.user_id == GameSourceObservationLink.user_id),
        )
        .where(
            GameSourceObservationLink.user_id == user_id,
            GameSourceObservationLink.observation_id.in_(set(targets)),
        )
    )
    history: dict[UUID, list[Any]] = {}
    for row in links:
        history.setdefault(row.observation_id, []).append(row)
    to_close: list[UUID] = []
    to_open: list[dict[str, Any]] = []
    for observation_id, game_id in targets.items():
        rows = history.get(observation_id, [])
        if rows:
            open_link = next((row for row in rows if row.unlinked_at is None), None)
            replaceable = (
                open_link is not None
                and open_link.deleted_at is not None
                and open_link.game_id != game_id
                and games[game_id] is None
            )
            if not replaceable or open_link is None:
                continue
            to_close.append(open_link.id)
        to_open.append(
            {
                "id": uuid4(),
                "user_id": user_id,
                "observation_id": observation_id,
                "game_id": game_id,
                "origin": RecordOrigin.INGEST.value,
                "linked_at": stamp,
            }
        )
    if to_close:
        await db.execute(
            update(GameSourceObservationLink)
            .where(GameSourceObservationLink.id.in_(to_close))
            .values(unlinked_at=stamp)
        )
    if to_open:
        await db.execute(pg_insert(GameSourceObservationLink).values(to_open))


@dataclass(frozen=True)
class InventoryResult:
    marked_unavailable: int
    applied: bool


async def complete_inventory(
    db: AsyncSession,
    user_id: UUID,
    ref: SourceConnectionRef,
    seen_external_ids: Collection[str],
    *,
    now: int | None = None,
) -> InventoryResult:
    """Record that the source delivered its COMPLETE inventory.

    Only here may a record absent from a payload become unavailable. Call it
    after a fully successful, fully paginated pull and never after a partial,
    failed or cancelled one. A disconnected connection is left untouched."""
    stamp = _now() if now is None else now
    connection = await find_connection(db, user_id, ref, lock=True)
    if connection is None or connection.disconnected_at is not None:
        return InventoryResult(0, False)
    seen = set(seen_external_ids)
    stale = [
        observation
        for observation in await db.scalars(
            select(GameSourceObservation)
            .where(
                GameSourceObservation.connection_id == connection.id,
                GameSourceObservation.availability != ObservationAvailability.UNAVAILABLE.value,
            )
            .order_by(GameSourceObservation.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if observation.external_id not in seen
    ]
    revisions = [
        _apply(
            observation,
            {},
            {"availability": ObservationAvailability.UNAVAILABLE.value},
            stamp,
        )
        for observation in stale
    ]
    db.add_all(revisions)
    connection.last_complete_inventory_at = stamp
    await db.flush()
    return InventoryResult(len(stale), True)


async def disconnect_connection(
    db: AsyncSession, user_id: UUID, ref: SourceConnectionRef, *, now: int | None = None
) -> bool:
    """Record an explicit disconnect. Observations, links and history are untouched."""
    connection = await find_connection(db, user_id, ref, lock=True)
    if connection is None or connection.disconnected_at is not None:
        return False
    connection.disconnected_at = _now() if now is None else now
    await db.flush()
    return True


async def relink_observation(
    db: AsyncSession,
    user_id: UUID,
    observation_id: UUID,
    game_id: UUID,
    *,
    origin: str,
    decision_id: UUID | None = None,
    now: int | None = None,
) -> GameSourceObservationLink | None:
    """Move a record to another tracked identity by an explicit decision.

    The observation, its external ID and its history are unchanged: the open
    link is ended and a new one opened. Ingestion cannot call this (`ingest` is
    not an accepted origin). Returns None when the record is already there."""
    if origin not in _RELINK_ORIGINS:
        raise ValueError(f"A relink needs a decision origin, not {origin!r}")
    stamp = _now() if now is None else now
    observation = await db.scalar(
        select(GameSourceObservation)
        .where(GameSourceObservation.id == observation_id, GameSourceObservation.user_id == user_id)
        .with_for_update()
    )
    game = await db.scalar(select(Game.id).where(Game.id == game_id, Game.user_id == user_id))
    if observation is None or game is None:
        raise LookupError("Observation or game not found for this user")
    current = await db.scalar(
        select(GameSourceObservationLink).where(
            GameSourceObservationLink.observation_id == observation_id,
            GameSourceObservationLink.unlinked_at.is_(None),
        )
    )
    if current is not None:
        if current.game_id == game_id:
            return None
        current.unlinked_at = max(stamp, current.linked_at)
        await db.flush()
    link = GameSourceObservationLink(
        user_id=user_id,
        observation_id=observation_id,
        game_id=game_id,
        origin=origin,
        decision_id=decision_id,
        linked_at=stamp,
    )
    db.add(link)
    await db.flush()
    return link
