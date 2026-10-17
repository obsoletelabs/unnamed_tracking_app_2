"""Pure rules that map one existing `games` row onto the identity model.

Nothing here touches the database. Titles are never used to decide identity: the
only evidence used is what the row itself records (its source, external ID,
Playnite GUID, parent link and status)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from src.database.models.game import GameStatus
from src.database.models.game_identity import (
    LEGACY_SOURCE_SCOPE,
    RelationshipKind,
)
from src.features.game_identity.names import classify_title, is_weak
from src.features.game_ingestion import legacy
from src.features.game_ingestion.contract import SourceConnectionRef, SourceObservationV1

DUPLICATE_SCOPE_PREFIX = "legacy-duplicate:"
UNATTRIBUTED_PLUGIN = "legacy-unattributed"
OWNED_COPY = "owned_copy"

HOST_BY_LABEL: dict[str, SourceConnectionRef] = {
    "steam": legacy.STEAM,
    "retroachievements": legacy.RETROACHIEVEMENTS,
    "playstation": legacy.PLAYSTATION,
}
# Achievement rows carry the provider label the sync used.
ACHIEVEMENT_PROVIDERS = {
    "Steam": "steam",
    "RetroAchievements": "retroachievements",
    "PlayStation": "playstation",
}
RELATIONSHIP_KINDS: dict[str, str] = {
    "mod": RelationshipKind.MOD_OF.value,
    "modpack": RelationshipKind.MODPACK_OF.value,
    "expansion": RelationshipKind.EXPANSION_OF.value,
    "dlc": RelationshipKind.DLC_OF.value,
    "standalone_expansion": RelationshipKind.STANDALONE_EXPANSION_OF.value,
    "total_conversion": RelationshipKind.TOTAL_CONVERSION_OF.value,
}
# Statuses a library sync can never produce from BACKLOG/WISHLIST except PLAYED and
# MASTERED, which it also never moves away from, so a lock costs sync nothing.
_USER_STATUSES = frozenset(
    {
        GameStatus.DROPPED,
        GameStatus.ON_HOLD,
        GameStatus.PLAYING,
        GameStatus.PLAYED,
        GameStatus.BEATEN,
        GameStatus.MASTERED,
    }
)


@dataclass(frozen=True)
class GameRow:  # pylint: disable=too-many-instance-attributes
    """The columns of a `games` row the backfill reads."""

    id: UUID
    title: str
    sort_title: str
    source: str | None
    external_id: str | None
    playnite_guid: UUID | None
    platform: str | None
    release_date: Any
    status: GameStatus
    playtime_seconds: int
    last_played_at: int | None
    favorite: bool
    stale_since: int | None
    parent_game_id: UUID | None
    relationship_type: str | None
    created_at: int
    deleted_at: int | None

    @property
    def order(self) -> tuple[bool, int, str]:
        """Live rows first, then oldest: the deterministic winner of a collision."""
        return (self.deleted_at is not None, self.created_at, str(self.id))


@dataclass(frozen=True)
class Claim:
    """One source record a row stands for."""

    connection: SourceConnectionRef
    external_id: str
    kind: str  # playnite | host | plugin | manual


def claims_for(game: GameRow) -> list[Claim]:
    """The source records a legacy row represents; a row can stand for several."""
    found: list[Claim] = []
    label = (game.source or "").strip().lower()
    external = (game.external_id or "").strip()
    if game.playnite_guid is not None:
        found.append(Claim(legacy.PLAYNITE, str(game.playnite_guid), "playnite"))
    if external and label in HOST_BY_LABEL:
        found.append(Claim(HOST_BY_LABEL[label], external, "host"))
    elif external:
        # A plugin's ID and account scope are not stored on the row; never guess them.
        found.append(
            Claim(
                legacy.plugin_connection(UNATTRIBUTED_PLUGIN, LEGACY_SOURCE_SCOPE, None),
                external,
                "plugin",
            )
        )
    if not found:
        found.append(Claim(legacy.MANUAL, str(game.id), "manual"))
    return found


def duplicate_connection(ref: SourceConnectionRef, game_id: UUID) -> SourceConnectionRef:
    """Where a row sharing another row's source record is kept (audit H4)."""
    return SourceConnectionRef(
        source_kind=ref.source_kind,
        source=ref.source,
        source_scope=f"{DUPLICATE_SCOPE_PREFIX}{game_id}",
        label=ref.label,
    )


def build_observation(
    game: GameRow, claim: Claim, achievements: dict[str, tuple[int, int]]
) -> SourceObservationV1 | None:
    """What the row records the source as having reported.

    Unknown stays unknown: zero playtime, a missing last played and a placeholder
    name are left out. The user's own choices (status of a non-Playnite game) are
    not evidence a source reported."""
    facts: dict[str, Any] = {}
    if game.playtime_seconds > 0:
        facts["playtime_seconds"] = game.playtime_seconds
    if game.last_played_at is not None:
        facts["last_played_at"] = game.last_played_at
    wishlist = game.status == GameStatus.WISHLIST
    if claim.kind == "host":
        facts["wishlisted" if wishlist else "owned"] = True
        total_unlocked = achievements.get(claim.connection.source)
        if total_unlocked is not None:
            facts["achievements_total"], facts["achievements_unlocked"] = total_unlocked
    elif claim.kind == "plugin" and not wishlist:
        facts["owned"] = True
    elif claim.kind == "playnite":
        facts["status"] = game.status.value
        facts["favorite"] = game.favorite
        if wishlist:
            facts["owned"] = False
    title = (
        None
        if is_weak(classify_title(game.title, source=game.source, external_id=game.external_id))
        else game.title
    )
    available = False if claim.kind in ("host", "plugin") and game.stale_since else None
    return legacy.build_observation(
        claim.external_id,
        title=title,
        platform=game.platform,
        release_date=game.release_date,
        available=available,
        **facts,
    )


def completion_lock(game: GameRow) -> bool:
    """True when the stored status is protected from sync as the user's (audit H9).

    Playnite owns the status of its games, so it stays unlocked there; every other
    status is a deliberate choice sync could not have made."""
    return game.playnite_guid is None and game.status in _USER_STATUSES


def collision_winners(rows: list[GameRow]) -> dict[tuple[str, str, str, str], UUID]:
    """The row that keeps a source record when several rows claim it (audit H4)."""
    winners: dict[tuple[str, str, str, str], GameRow] = {}
    for game in rows:
        for claim in claims_for(game):
            key = connection_key(claim)
            if key not in winners or game.order < winners[key].order:
                winners[key] = game
    return {key: game.id for key, game in winners.items()}


def connection_key(claim: Claim) -> tuple[str, str, str, str]:
    ref = claim.connection
    return (ref.source_kind, ref.source, ref.source_scope, claim.external_id)
