"""Compatibility adapter between the existing import routes and the registry.

Steam, RetroAchievements, PlayStation, Playnite and manual creation keep their
routes, payloads, status codes and game-matching rules. After a route has
resolved its `games` row the way it always has, it hands the adapter what the
source reported; the adapter records that as a scoped observation and, for a
record that was never linked, attaches it to that row. Nothing here changes
which game a record is, and none of it decides identity.

Host sources predate per-account identity, so they use the `legacy` scope and
manual entries use `local` (never guessed, audit H5/H21).
"""

from __future__ import annotations

import re
import time
from collections.abc import Collection, Iterable, Mapping
from typing import Any
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models.game import Game
from src.database.models.game_identity import (
    LEGACY_SOURCE_SCOPE,
    MANUAL_SOURCE_SCOPE,
    GameSourceConnection,
    GameSourceObservation,
    HostSource,
    SourceKind,
)
from src.features.game_identity.review import run_matching
from src.features.game_identity import names
from src.features.game_identity.names import ObservedName
from src.features.game_ingestion.contract import (
    ObservationBatchV1,
    ObservedFacts,
    SourceConnectionRef,
    SourceObservationV1,
)
from src.features.game_ingestion.service import (
    BatchResult,
    complete_inventory,
    disconnect_connection,
    ingest_batch,
)

_BATCH_SIZE = 2000
_PROVIDER_KEY = re.compile(r"[a-z0-9._-]{1,50}")
_FACT_FIELDS = tuple(ObservedFacts.model_fields)


def host_connection(source: HostSource) -> SourceConnectionRef:
    return SourceConnectionRef(
        source_kind=SourceKind.HOST.value, source=source.value, source_scope=LEGACY_SOURCE_SCOPE
    )


STEAM = host_connection(HostSource.STEAM)
RETROACHIEVEMENTS = host_connection(HostSource.RETROACHIEVEMENTS)
PLAYSTATION = host_connection(HostSource.PLAYSTATION)
PLAYNITE = host_connection(HostSource.PLAYNITE)
MANUAL = SourceConnectionRef(
    source_kind=SourceKind.MANUAL.value, source="manual", source_scope=MANUAL_SOURCE_SCOPE
)


def plugin_connection(plugin_id: str, source_scope: str, label: str | None) -> SourceConnectionRef:
    return SourceConnectionRef(
        source_kind=SourceKind.PLUGIN.value,
        source=plugin_id,
        source_scope=source_scope,
        label=label,
    )


def _clip(value: Any, limit: int) -> Any:
    return value[:limit] if isinstance(value, str) else value


def build_observation(
    external_id: str | int | None,
    *,
    title: str | None = None,
    platform: str | None = None,
    edition: str | None = None,
    release_date: Any = None,
    provider_ids: Mapping[str, Any] | None = None,
    available: bool | None = None,
    source_updated_at: int | None = None,
    **facts: Any,
) -> SourceObservationV1 | None:
    """Build an observation from loosely typed legacy data.

    Anything missing, blank or out of the contract's bounds is dropped as
    unknown rather than failing an import that has always succeeded."""
    key = str(external_id).strip() if external_id is not None else ""
    if not key or len(key) > 256:
        return None
    clean_ids = {
        str(name): str(value)[:256]
        for name, value in (provider_ids or {}).items()
        if value not in (None, "") and _PROVIDER_KEY.fullmatch(str(name))
    }
    clean_facts: dict[str, Any] = {}
    for name in _FACT_FIELDS:
        value = facts.get(name)
        if value is None:
            continue
        if isinstance(value, list):
            value = [_clip(item, 128) for item in value[:50]]
        clean_facts[name] = _clip(value, 20000 if name == "description" else 200)
    clean_facts = {name: value for name, value in clean_facts.items() if value is not None}
    try:
        return SourceObservationV1(
            external_id=key,
            title=_clip(title.strip(), 500) if title and title.strip() else None,
            platform=_clip(platform, 50) or None,
            edition=_clip(edition, 100) or None,
            release_date=release_date,
            provider_ids=clean_ids or None,
            facts=ObservedFacts.model_validate(clean_facts) if clean_facts else None,
            source_updated_at=source_updated_at,
            available=available,
        )
    except ValidationError:
        # One malformed optional value must not abort the source's whole import.
        return SourceObservationV1(external_id=key, available=available)


async def record_observations(
    db: AsyncSession,
    user_id: UUID,
    connection: SourceConnectionRef,
    entries: Iterable[tuple[UUID, SourceObservationV1 | None]],
) -> list[BatchResult]:
    """Persist what a legacy route reported and attach each record to its resolved game."""
    resolved: dict[str, tuple[UUID, SourceObservationV1]] = {}
    for game_id, observation in entries:
        if observation is not None:
            resolved[observation.external_id] = (game_id, observation)
    results = []
    keys = list(resolved)
    for start in range(0, len(keys), _BATCH_SIZE):
        chunk = keys[start : start + _BATCH_SIZE]
        results.append(
            await ingest_batch(
                db,
                user_id,
                ObservationBatchV1(
                    connection=connection, observations=[resolved[key][1] for key in chunk]
                ),
                attach={key: resolved[key][0] for key in chunk},
            )
        )
    await _reconcile_names(
        db, user_id, resolved, results, reviewed=connection in (PLAYNITE, MANUAL)
    )
    return results


async def _reconcile_names(
    db: AsyncSession,
    user_id: UUID,
    resolved: Mapping[str, tuple[UUID, SourceObservationV1]],
    results: Iterable[BatchResult],
    *,
    reviewed: bool,
) -> None:
    """Apply the name each source reported under the quality, provenance and lock rules
    (#98). The raw name is already on the observation whatever happens here."""
    observation_ids = {r.external_id: r.observation_id for b in results for r in b.results}
    observed = {
        game_id: ObservedName(observation.title, observation_ids.get(key), int(time.time()))
        for key, (game_id, observation) in resolved.items()
        if observation.title and key in observation_ids
    }
    if not observed:
        return
    # The import route has just resolved these rows, so they are normally already loaded.
    games = [
        obj
        for obj in db.identity_map.values()
        if isinstance(obj, Game) and obj.user_id == user_id and obj.id in observed
    ]
    missing = set(observed) - {game.id for game in games}
    if missing:
        games += (
            await db.scalars(
                select(Game)
                .where(Game.user_id == user_id, Game.id.in_(missing))
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).all()
    await names.reconcile_games(db, user_id, games, observed, reviewed_renames=reviewed)


async def record_complete_inventory(
    db: AsyncSession,
    user_id: UUID,
    connection: SourceConnectionRef,
    seen_external_ids: Collection[str],
) -> None:
    """Only after a pull that completed without error."""
    await complete_inventory(db, user_id, connection, seen_external_ids)
    # A confirmed-complete inventory is the only moment automatic matching may merge (#99).
    await run_matching(db, user_id)


async def record_disconnect(
    db: AsyncSession, user_id: UUID, connection: SourceConnectionRef
) -> None:
    """Mirror an explicit account disconnect; credentials are cleared by the caller."""
    await disconnect_connection(db, user_id, connection)


# --- Playnite and manual entries -------------------------------------------------------------

_IDENTITY_FIELDS = ("title", "platform", "release_date")
_GAME_FACT_FIELDS = (
    "description",
    "developer",
    "publisher",
    "series",
    "tags",
    "features",
    "favorite",
    "playtime_seconds",
)


def observation_from_game_fields(
    external_id: str, values: Mapping[str, Any]
) -> SourceObservationV1 | None:
    """Map the fields a create/update payload actually carried to an observation.

    `values` holds only fields the client sent; unsent fields stay unknown."""
    facts = {name: values.get(name) for name in _GAME_FACT_FIELDS}
    status = values.get("status")
    if status is not None:
        facts["status"] = getattr(status, "value", status)
    return build_observation(
        external_id,
        title=values.get("title"),
        platform=values.get("platform"),
        release_date=values.get("release_date"),
        provider_ids=values.get("provider_ids"),
        **facts,
    )


async def record_playnite_fields(
    db: AsyncSession, user_id: UUID, game_id: UUID, guid: UUID | str, values: Mapping[str, Any]
) -> None:
    await record_observations(
        db, user_id, PLAYNITE, [(game_id, observation_from_game_fields(str(guid), values))]
    )


async def has_manual_observation(db: AsyncSession, user_id: UUID, game_id: UUID) -> bool:
    return (
        await db.scalar(
            select(GameSourceObservation.id)
            .join(
                GameSourceConnection,
                (GameSourceConnection.id == GameSourceObservation.connection_id)
                & (GameSourceConnection.user_id == GameSourceObservation.user_id),
            )
            .where(
                GameSourceObservation.user_id == user_id,
                GameSourceObservation.external_id == str(game_id),
                GameSourceConnection.source_kind == MANUAL.source_kind,
                GameSourceConnection.source == MANUAL.source,
                GameSourceConnection.source_scope == MANUAL.source_scope,
            )
        )
    ) is not None


async def record_manual_fields(
    db: AsyncSession, user_id: UUID, game_id: UUID, values: Mapping[str, Any]
) -> None:
    """A manual entry's stable source ID is its own game ID."""
    await record_observations(
        db, user_id, MANUAL, [(game_id, observation_from_game_fields(str(game_id), values))]
    )
