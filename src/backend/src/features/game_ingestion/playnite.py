"""Playnite as a source of observations (#100).

The Playnite extension is evidence, not authority: it reports what its library
holds, keyed by the stable Playnite game ID, and never creates, merges or
splits canonical identities. Two paths share one observation record:

* the original create/update routes (kept for every deployed extension), which
  record the fields a request carried through `legacy.record_playnite_fields`;
* the versioned evidence API in `api/routes/playnite.py`, which adds what those
  routes cannot carry (platform, last played, installed, ownership,
  achievement counts) and the explicit complete-inventory signal.

Every record is keyed by `(connection, playnite_id)`. The default connection is
the `legacy` Playnite scope used by the original routes, so an upgraded
extension continues the same observations instead of starting new ones. An
extension may name its library (`library_id`) to keep several Playnite
installations apart.
"""

from __future__ import annotations

import logging
import re
from datetime import date
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models.game import Game, GameStatus
from src.database.models.game_identity import SourceKind
from src.features.game_ingestion import legacy
from src.features.game_ingestion.contract import SourceConnectionRef
from src.features.game_ingestion.service import (
    InventoryResult,
    complete_inventory,
    find_connection,
)

logger = logging.getLogger(__name__)

CONTRACT_NAME = "playnite-observations"
CURRENT_VERSION = 1
SUPPORTED_VERSIONS = (1,)
MAX_BATCH = 500
MAX_INVENTORY = 50_000
FEATURES = (
    "observations",
    "complete-inventory",
    "library-scope",
    "platform",
    "last-played",
    "installed",
    "ownership",
    "achievement-counts",
)
STATUS_LOCK = "status"
TITLE_LOCK = "title"
_LIBRARY = re.compile(r"[A-Za-z0-9._-]{1,64}")


class UnsupportedContract(ValueError):
    """The extension speaks a contract version this host does not."""

    def __init__(self, version: int) -> None:
        super().__init__(f"Contract version {version} is not supported")
        self.version = version


class PlayniteGameV1(BaseModel):
    """What one Playnite library entry reports. Omitted means unknown, never cleared."""

    model_config = ConfigDict(extra="forbid")

    playnite_id: UUID
    name: str | None = Field(default=None, min_length=1, max_length=500)
    platforms: list[str] | None = Field(default=None, max_length=20)
    release_date: date | None = None
    playtime_seconds: int | None = Field(default=None, ge=0, le=2_000_000_000)
    last_played_at: int | None = Field(default=None, ge=0)
    installed: bool | None = None
    owned: bool | None = None
    favorite: bool | None = None
    status: GameStatus | None = None
    achievements_total: int | None = Field(default=None, ge=0)
    achievements_unlocked: int | None = Field(default=None, ge=0)
    source: str | None = Field(default=None, max_length=50)
    developer: str | None = Field(default=None, max_length=200)
    publisher: str | None = Field(default=None, max_length=200)
    series: str | None = Field(default=None, max_length=200)
    modified_at: int | None = Field(default=None, ge=0)

    @field_validator("platforms")
    @classmethod
    def clean_platforms(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        return [item.strip()[:50] for item in value if item and item.strip()]


class PlayniteBatchV1(BaseModel):
    """A bounded page of entries from one Playnite library."""

    model_config = ConfigDict(extra="forbid")

    contract_version: int
    library_id: str | None = Field(default=None, max_length=64)
    games: list[PlayniteGameV1] = Field(min_length=1, max_length=MAX_BATCH)

    @field_validator("library_id")
    @classmethod
    def valid_library(cls, value: str | None) -> str | None:
        if value is not None and not _LIBRARY.fullmatch(value):
            raise ValueError("Use letters, digits, '.', '_' or '-' for the library ID")
        return value

    @field_validator("games")
    @classmethod
    def distinct(cls, items: list[PlayniteGameV1]) -> list[PlayniteGameV1]:
        if len({item.playnite_id for item in items}) != len(items):
            raise ValueError("Each batch entry needs a distinct Playnite ID")
        return items


class PlayniteInventoryV1(BaseModel):
    """Every Playnite ID the library holds, sent only after a complete, uncancelled read."""

    model_config = ConfigDict(extra="forbid")

    contract_version: int
    library_id: str | None = Field(default=None, max_length=64)
    playnite_ids: list[UUID] = Field(max_length=MAX_INVENTORY)

    @field_validator("library_id")
    @classmethod
    def valid_library(cls, value: str | None) -> str | None:
        return PlayniteBatchV1.valid_library(value)  # type: ignore[no-any-return]


def require_supported(version: int) -> None:
    if version not in SUPPORTED_VERSIONS:
        raise UnsupportedContract(version)


def contract_description() -> dict[str, Any]:
    return {
        "contract": CONTRACT_NAME,
        "current_version": CURRENT_VERSION,
        "supported_versions": list(SUPPORTED_VERSIONS),
        "features": list(FEATURES),
        "limits": {"batch": MAX_BATCH, "inventory": MAX_INVENTORY},
        "legacy_routes": ["/api/game/create", "/api/game/update/{id}", "/api/game/list"],
    }


def connection_for(library_id: str | None) -> SourceConnectionRef:
    if library_id is None:
        return legacy.PLAYNITE
    return SourceConnectionRef(
        source_kind=SourceKind.HOST.value,
        source="playnite",
        source_scope=f"library:{library_id}",
        label="Playnite",
    )


def _observation(game: PlayniteGameV1):
    facts: dict[str, Any] = {
        "playtime_seconds": game.playtime_seconds,
        "last_played_at": game.last_played_at,
        "installed": game.installed,
        "owned": game.owned,
        "favorite": game.favorite,
        "status": game.status.value if game.status else None,
        "achievements_total": game.achievements_total,
        "achievements_unlocked": game.achievements_unlocked,
        "developer": game.developer,
        "publisher": game.publisher,
        "series": game.series,
    }
    return legacy.build_observation(
        str(game.playnite_id),
        title=game.name,
        platform=game.platforms[0] if game.platforms else None,
        release_date=game.release_date,
        source_updated_at=game.modified_at,
        available=True,
        **facts,
    )


async def _resolve(
    db: AsyncSession, user_id: UUID, ids: list[UUID]
) -> tuple[dict[UUID, tuple[UUID, int | None]], dict[UUID, UUID]]:
    """Active games (with their last played) and trashed games by Playnite ID."""
    rows = (
        await db.execute(
            select(Game.playnite_guid, Game.id, Game.deleted_at, Game.last_played_at).where(
                Game.user_id == user_id, Game.playnite_guid.in_(ids)
            )
        )
    ).all()
    active: dict[UUID, tuple[UUID, int | None]] = {}
    trashed: dict[UUID, UUID] = {}
    for guid, game_id, deleted_at, last_played in rows:
        if deleted_at is None:
            active.setdefault(guid, (game_id, last_played))
        else:
            trashed.setdefault(guid, game_id)
    return active, trashed


async def record_batch(
    db: AsyncSession, user_id: UUID, batch: PlayniteBatchV1
) -> list[dict[str, Any]]:
    """Record a page of Playnite entries as observations. The caller commits.

    An entry is attached only to the game that already carries its Playnite ID;
    an unknown ID is reported back (`unlinked`) so the extension creates it
    through the create route, and an ID whose game is in Trash is reported, not
    recreated. Nothing here changes a game's title, status or identity."""
    require_supported(batch.contract_version)
    active, trashed = await _resolve(db, user_id, [item.playnite_id for item in batch.games])
    entries = [
        (active[item.playnite_id][0], _observation(item))
        for item in batch.games
        if item.playnite_id in active
    ]
    outcomes: dict[str, str] = {}
    if entries:
        for batch_result in await legacy.record_observations(
            db, user_id, connection_for(batch.library_id), entries
        ):
            outcomes.update({row.external_id: row.outcome for row in batch_result.results})
    out: list[dict[str, Any]] = []
    for item in batch.games:
        key = str(item.playnite_id)
        if item.playnite_id in active:
            game_id, last_played = active[item.playnite_id]
            if item.last_played_at is not None and item.last_played_at > (last_played or 0):
                await db.execute(
                    update(Game)
                    .where(Game.id == game_id)
                    .values(last_played_at=item.last_played_at)
                )
            out.append(
                {
                    "playnite_id": key,
                    "game_id": str(game_id),
                    "outcome": outcomes.get(key, "unchanged"),
                }
            )
            continue
        trashed_id = trashed.get(item.playnite_id)
        outcome = "in_trash" if trashed_id else "unlinked"
        out.append(
            {
                "playnite_id": key,
                "game_id": str(trashed_id) if trashed_id else None,
                "outcome": outcome,
            }
        )
    counts: dict[str, int] = {}
    for row in out:
        counts[row["outcome"]] = counts.get(row["outcome"], 0) + 1
    logger.info(
        "playnite observations user=%s library=%s entries=%d outcomes=%s",
        user_id,
        batch.library_id or "default",
        len(out),
        dict(sorted(counts.items())),
    )
    return out


async def record_inventory(
    db: AsyncSession, user_id: UUID, body: PlayniteInventoryV1
) -> InventoryResult:
    """The only place an absent Playnite entry becomes unavailable (never deleted)."""
    require_supported(body.contract_version)
    result = await complete_inventory(
        db,
        user_id,
        connection_for(body.library_id),
        {str(item) for item in body.playnite_ids},
    )
    logger.info(
        "playnite inventory user=%s library=%s reported=%d applied=%s marked_unavailable=%d",
        user_id,
        body.library_id or "default",
        len(body.playnite_ids),
        result.applied,
        result.marked_unavailable,
    )
    return result


async def has_connection(db: AsyncSession, user_id: UUID, library_id: str | None) -> bool:
    return await find_connection(db, user_id, connection_for(library_id)) is not None


def guard_update(game: Game, updates: dict[str, Any], actor: object) -> frozenset[str] | None:
    """Drop what a Playnite update must not change; return the locks to restore.

    Applies only to an API-key update of a game that carries a Playnite ID.
    A name the user locked is kept (the report is still recorded as evidence),
    not rejected: a 403 would fail the entire update, including playtime. A
    status the user set by hand is kept the same way. Playnite's own changes
    never become user locks, so a later rename from Playnite still applies."""
    if getattr(actor, "credential_kind", None) != "api_key" or game.playnite_guid is None:
        return None
    locked = frozenset(game.locked_fields or [])
    if TITLE_LOCK in locked:
        updates.pop("title", None)
        updates.pop("sort_title", None)
    if STATUS_LOCK in locked:
        updates.pop("status", None)
        updates.pop("completion_date", None)
    return locked


def restore_locks(game: Game, kept: frozenset[str] | None) -> None:
    """After the update: the source's own changes are never the user's protection."""
    if kept is not None:
        game.locked_fields = sorted(kept)
