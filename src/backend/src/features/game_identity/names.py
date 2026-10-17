"""Game name quality, provenance and placeholder repair (#98).

A game's effective name is one `game_field_values` row (`field = 'title'`) that
records where the name came from, how trustworthy it is and whether the user
locked it. `games.title` stays the value every consumer reads; nothing here
changes a game's ID, folder, external ID, Playnite GUID or tracking data, and no
game is ever created or deleted.

Precedence (`decide_title`):

1. A locked name (the user's `locked_fields` entry) is never replaced silently.
   A different, legitimate source or metadata name becomes a `proposed` value the
   user answers with Update or Keep current.
2. A placeholder or low-quality name ("Steam App 12345", "Unknown Game") is
   replaced by any better name.
3. A placeholder or low-quality candidate never replaces a name that is better.
4. A better-quality name replaces an unlocked one; a legitimate but lower-ranked
   candidate (a source name against canonical metadata) is proposed instead.

The raw name every source reported stays on its observation and revisions, so a
decision can be audited and reversed.
"""

from __future__ import annotations

import re
import time
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Literal
from uuid import UUID
from weakref import WeakKeyDictionary

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models.game import Game
from src.database.models.game_field_change import GameFieldChange
from src.database.models.game_identity import (
    GameSourceObservation,
    GameSourceObservationLink,
    GameSourceObservationRevision,
)
from src.database.models.game_identity_tracking import (
    FieldQuality,
    FieldValueOrigin,
    FieldValueState,
    GameFieldValue,
    LockOrigin,
)

TITLE = "title"
Action = Literal["unchanged", "keep", "replace", "propose"]

_RANK = {
    FieldQuality.PLACEHOLDER.value: 0,
    FieldQuality.LOW.value: 1,
    FieldQuality.STANDARD.value: 2,
    FieldQuality.CANONICAL.value: 3,
}

# Whole-title patterns only, so "Untitled Goose Game" or "App 12 Days" are never
# placeholders. `None` providers apply to every source.
_PLACEHOLDERS: tuple[tuple[frozenset[str] | None, re.Pattern[str]], ...] = (
    (None, re.compile(r"steam[\s_-]*app[\s_:#-]*\d+")),
    (None, re.compile(r"app[\s_-]*id[\s_:#-]*\d+")),
    (frozenset({"steam"}), re.compile(r"app[\s_:#-]*\d+")),
    (None, re.compile(r"(unknown|unnamed|untitled)([\s_-]+game)?")),
    (None, re.compile(r"game[\s_:#-]*\d{4,}")),
    (None, re.compile(r"unknown[\s_-]+title")),
)
_LOW = re.compile(r"[\d\s._#:/-]*|n/?a|none|null|tbd|new[\s_-]+game")
_WORD = re.compile(r"\s+")


def classify_title(
    title: str | None, *, source: str | None = None, external_id: str | None = None
) -> FieldQuality:
    """Quality of a name as a source reported it, before any metadata is known.

    Recognised placeholders are `placeholder`; empty, purely numeric or filler
    names are `low`; everything else, however unusual, is `standard`."""
    text = _WORD.sub(" ", (title or "").strip()).casefold()
    if not text:
        return FieldQuality.LOW
    provider = (source or "").casefold()
    for providers, pattern in _PLACEHOLDERS:
        if (providers is None or provider in providers) and pattern.fullmatch(text):
            return FieldQuality.PLACEHOLDER
    if external_id and text == external_id.strip().casefold() and any(c.isdigit() for c in text):
        return FieldQuality.PLACEHOLDER
    if _LOW.fullmatch(text):
        return FieldQuality.LOW
    return FieldQuality.STANDARD


def is_weak(quality: str | FieldQuality) -> bool:
    return _RANK[getattr(quality, "value", quality)] < _RANK[FieldQuality.STANDARD.value]


@dataclass(frozen=True)
class Decision:
    action: Action
    reason: str


def decide_title(
    current_quality: str | FieldQuality,
    locked: bool,
    candidate_quality: str | FieldQuality,
    *,
    same: bool = False,
) -> Decision:
    """What to do when a source or metadata provider offers `candidate`."""
    current = _RANK[getattr(current_quality, "value", current_quality)]
    offered = _RANK[getattr(candidate_quality, "value", candidate_quality)]
    if same:
        return Decision("unchanged", "same name")
    if offered < _RANK[FieldQuality.STANDARD.value]:
        return Decision("keep", "the offered name is a placeholder or low quality")
    if locked:
        return Decision("propose", "the current name is locked")
    if current < _RANK[FieldQuality.STANDARD.value]:
        return Decision("replace", "the current name is a placeholder")
    if offered >= current:
        return Decision("replace", "better or equal trust")
    return Decision("propose", "the current name came from a more trusted source")


@dataclass(frozen=True)
class NameHint:
    """Provenance a caller knows but the database row does not yet carry."""

    origin: FieldValueOrigin
    quality: FieldQuality


_HINTS: WeakKeyDictionary[Game, NameHint] = WeakKeyDictionary()


def note_title_change(game: Game, *, trusted_metadata: bool) -> None:
    """Remember that the game's current title was just set from a provider, so
    the next `reconcile_games` records it as metadata-quality rather than legacy."""
    quality = classify_title(game.title, source=game.source, external_id=game.external_id)
    if trusted_metadata and not is_weak(quality):
        quality = FieldQuality.CANONICAL
    _HINTS[game] = NameHint(
        FieldValueOrigin.METADATA if trusted_metadata else FieldValueOrigin.SOURCE, quality
    )


def apply_candidate_title(
    game: Game, candidate: str, *, trusted_metadata: bool = False
) -> Decision:
    """Pure in-memory rule used where a provider offers a name without a database
    session. Returns the decision; a `replace` has been applied to `game`."""
    quality = classify_title(candidate, source=game.source, external_id=game.external_id)
    if trusted_metadata and not is_weak(quality):
        quality = FieldQuality.CANONICAL
    current = classify_title(game.title, source=game.source, external_id=game.external_id)
    hint = _HINTS.get(game)
    if hint is not None and not is_weak(current):
        current = hint.quality
    decision = decide_title(
        current, TITLE in (game.locked_fields or []), quality, same=candidate == game.title
    )
    if decision.action == "replace":
        game.title = candidate
        if "sort_title" not in (game.locked_fields or []):
            game.sort_title = candidate.lower()
        note_title_change(game, trusted_metadata=trusted_metadata)
        _HINTS[game] = NameHint(_HINTS[game].origin, quality)
    return decision


@dataclass
class ObservedName:
    title: str
    observation_id: UUID | None
    observed_at: int | None = None
    trusted_metadata: bool = False


@dataclass
class ReconcileReport:
    checked: int = 0
    tracked: int = 0
    replaced: list[UUID] = field(default_factory=list)
    proposed: list[UUID] = field(default_factory=list)
    unresolved: list[UUID] = field(default_factory=list)


def _now() -> int:
    return int(time.time())


async def _current_values(
    db: AsyncSession, user_id: UUID, game_ids: Collection[UUID]
) -> dict[UUID, GameFieldValue]:
    rows = await db.scalars(
        select(GameFieldValue)
        .where(
            GameFieldValue.user_id == user_id,
            GameFieldValue.game_id.in_(game_ids),
            GameFieldValue.field == TITLE,
            GameFieldValue.state == FieldValueState.CURRENT.value,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return {row.game_id: row for row in rows if row.game_id is not None}


def _supersede(row: GameFieldValue, stamp: int) -> None:
    row.state = FieldValueState.SUPERSEDED.value
    row.resolved_at = stamp


def _new_value(
    game: Game,
    *,
    value: str,
    state: FieldValueState,
    origin: FieldValueOrigin,
    quality: FieldQuality,
    observation_id: UUID | None,
    stamp: int,
    observed_at: int | None = None,
) -> GameFieldValue:
    locked = TITLE in (game.locked_fields or [])
    return GameFieldValue(
        user_id=game.user_id,
        game_id=game.id,
        field=TITLE,
        value=value,
        state=state.value,
        origin=origin.value,
        observation_id=observation_id,
        quality=quality.value,
        observed_at=observed_at,
        edited_at=stamp if origin == FieldValueOrigin.USER else None,
        recorded_at=stamp,
        locked=locked and state == FieldValueState.CURRENT,
        lock_origin=(
            (LockOrigin.USER if origin == FieldValueOrigin.USER else LockOrigin.LEGACY).value
            if locked and state == FieldValueState.CURRENT
            else None
        ),
    )


def _origin_for(origin: FieldValueOrigin, observation_id: UUID | None) -> FieldValueOrigin:
    """Values from a source or metadata must name an observation (database rule)."""
    if origin in (FieldValueOrigin.SOURCE, FieldValueOrigin.METADATA) and observation_id is None:
        return FieldValueOrigin.SYSTEM
    return origin


async def _open_proposals(
    db: AsyncSession, user_id: UUID, game_ids: Collection[UUID]
) -> dict[UUID, list[GameFieldValue]]:
    rows = await db.scalars(
        select(GameFieldValue)
        .where(
            GameFieldValue.user_id == user_id,
            GameFieldValue.game_id.in_(game_ids),
            GameFieldValue.field == TITLE,
            GameFieldValue.state.in_(
                [FieldValueState.PROPOSED.value, FieldValueState.REJECTED.value]
            ),
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    grouped: dict[UUID, list[GameFieldValue]] = {}
    for row in rows:
        if row.game_id is not None:
            grouped.setdefault(row.game_id, []).append(row)
    return grouped


async def _track_current(
    db: AsyncSession,
    game: Game,
    current: GameFieldValue | None,
    observed: ObservedName | None,
    stamp: int,
) -> GameFieldValue:
    """Make the current value row match `games.title`, recording who set it."""
    locked = TITLE in (game.locked_fields or [])
    quality = classify_title(game.title, source=game.source, external_id=game.external_id)
    hint = _HINTS.pop(game, None)
    if current is not None and current.value == game.title:
        if hint is not None and _RANK[hint.quality.value] > _RANK[current.quality]:
            current.quality = hint.quality.value
        want = LockOrigin.USER.value if current.origin == "user" else LockOrigin.LEGACY.value
        if current.locked != locked or (locked and current.lock_origin is None):
            current.locked = locked
            current.lock_origin = want if locked else None
        return current
    origin = FieldValueOrigin.LEGACY
    observation_id = None
    if hint is not None:
        origin, quality = hint.origin, hint.quality
    elif observed is not None and observed.title == game.title:
        origin = FieldValueOrigin.METADATA if observed.trusted_metadata else FieldValueOrigin.SOURCE
        observation_id = observed.observation_id
        if observed.trusted_metadata and not is_weak(quality):
            quality = FieldQuality.CANONICAL
    elif current is not None and locked:
        # a locked name that changed under us was edited by the user
        origin = FieldValueOrigin.USER
    if hint is not None and observed is not None and observed.title == game.title:
        observation_id = observed.observation_id
    origin = _origin_for(origin, observation_id)
    if origin == FieldValueOrigin.USER:
        observation_id = None
    if current is not None:
        _supersede(current, stamp)
        await db.flush()
    row = _new_value(
        game,
        value=game.title,
        state=FieldValueState.CURRENT,
        origin=origin,
        quality=quality,
        observation_id=observation_id,
        stamp=stamp,
        observed_at=observed.observed_at if observed and observed.title == game.title else None,
    )
    db.add(row)
    return row


def _propose(
    db: AsyncSession,
    game: Game,
    candidate: ObservedName,
    quality: FieldQuality,
    open_rows: list[GameFieldValue],
    *,
    stamp: int,
) -> bool:
    for row in open_rows:
        same = row.observation_id == candidate.observation_id and row.value == candidate.title
        if same:
            # an open proposal, or one the user already answered with Keep current
            return False
        if row.state == FieldValueState.PROPOSED.value and (
            row.observation_id == candidate.observation_id
        ):
            _supersede(row, stamp)
    origin = _origin_for(
        FieldValueOrigin.METADATA if candidate.trusted_metadata else FieldValueOrigin.SOURCE,
        candidate.observation_id,
    )
    db.add(
        _new_value(
            game,
            value=candidate.title,
            state=FieldValueState.PROPOSED,
            origin=origin,
            quality=quality,
            observation_id=candidate.observation_id,
            stamp=stamp,
            observed_at=candidate.observed_at,
        )
    )
    return True


def _rename(db: AsyncSession, game: Game, new_title: str, stamp: int) -> None:
    db.add(
        GameFieldChange(
            game_id=game.id,
            field_name=TITLE,
            old_value=game.title,
            new_value=new_title,
            changed_at=stamp,
        )
    )
    game.title = new_title
    if "sort_title" not in (game.locked_fields or []):
        game.sort_title = new_title.lower()


async def reconcile_games(  # pylint: disable=too-many-locals
    db: AsyncSession,
    user_id: UUID,
    games: Iterable[Game],
    observed: Mapping[UUID, ObservedName] | None = None,
    *,
    reviewed_renames: bool = False,
) -> ReconcileReport:
    """Record the provenance of each game's name and apply `observed` names.

    Idempotent. `observed` maps a game ID to the name a source or provider just
    reported; the raw name stays on its observation whatever the outcome. With
    `reviewed_renames`, a source whose existing records are renamed through their own
    update call (Playnite, manual entries) never renames a real name silently: a
    different real name is proposed instead."""
    stamp = _now()
    items = {game.id: game for game in games if game.user_id == user_id}
    report = ReconcileReport(checked=len(items))
    if not items:
        return report
    values = await _current_values(db, user_id, items)
    open_rows = await _open_proposals(db, user_id, items)
    for game_id, game in items.items():
        seen = (observed or {}).get(game_id)
        before = values.get(game_id)
        current = await _track_current(db, game, before, seen, stamp)
        report.tracked += before is None or before is not current
        if seen is None or not seen.title.strip():
            continue
        quality = classify_title(seen.title, source=game.source, external_id=game.external_id)
        if seen.trusted_metadata and not is_weak(quality):
            quality = FieldQuality.CANONICAL
        decision = decide_title(
            current.quality,
            TITLE in (game.locked_fields or []),
            quality,
            same=seen.title == game.title,
        )
        if reviewed_renames and decision.action == "replace" and not is_weak(current.quality):
            decision = Decision("propose", "renames from this source are reviewed")
        if decision.action == "replace":
            _supersede(current, stamp)
            await db.flush()
            _rename(db, game, seen.title, stamp)
            origin = _origin_for(
                FieldValueOrigin.METADATA if seen.trusted_metadata else FieldValueOrigin.SOURCE,
                seen.observation_id,
            )
            db.add(
                _new_value(
                    game,
                    value=seen.title,
                    state=FieldValueState.CURRENT,
                    origin=origin,
                    quality=quality,
                    observation_id=seen.observation_id,
                    stamp=stamp,
                    observed_at=seen.observed_at,
                )
            )
            report.replaced.append(game_id)
        elif decision.action == "propose":
            if _propose(db, game, seen, quality, open_rows.get(game_id, []), stamp=stamp):
                report.proposed.append(game_id)
        # a proposal that now equals the effective name has nothing left to ask
        for row in open_rows.get(game_id, []):
            if row.state == FieldValueState.PROPOSED.value and row.value == game.title:
                _supersede(row, stamp)
    await db.flush()
    return report


async def linked_observations(
    db: AsyncSession, user_id: UUID, game_id: UUID
) -> list[GameSourceObservation]:
    """The source records currently attached to a game."""
    return list(
        (
            await db.scalars(
                select(GameSourceObservation)
                .join(
                    GameSourceObservationLink,
                    (GameSourceObservationLink.observation_id == GameSourceObservation.id)
                    & (GameSourceObservationLink.user_id == GameSourceObservation.user_id),
                )
                .where(
                    GameSourceObservation.user_id == user_id,
                    GameSourceObservationLink.game_id == game_id,
                    GameSourceObservationLink.unlinked_at.is_(None),
                )
            )
        ).all()
    )


async def _best_source_name(db: AsyncSession, user_id: UUID, game: Game) -> ObservedName | None:
    """The newest good name among the observations linked to `game`, including
    names earlier revisions reported before a later low-quality one."""
    observations = await linked_observations(db, user_id, game.id)
    best: ObservedName | None = None
    for observation in observations:
        names = [(observation.observed_title, observation.last_observed_at)]
        revisions = await db.scalars(
            select(GameSourceObservationRevision)
            .where(
                GameSourceObservationRevision.observation_id == observation.id,
                GameSourceObservationRevision.user_id == user_id,
            )
            .order_by(GameSourceObservationRevision.revision.desc())
        )
        names += [(rev.changes.get(TITLE), rev.observed_at) for rev in revisions]
        for title, stamp in names:
            if not isinstance(title, str) or is_weak(
                classify_title(title, source=game.source, external_id=game.external_id)
            ):
                continue
            if best is None or (stamp or 0) > (best.observed_at or 0):
                best = ObservedName(title, observation.id, stamp)
            break
    return best


async def repair_games(
    db: AsyncSession, user_id: UUID, *, dry_run: bool = False, limit: int | None = None
) -> ReconcileReport:
    """Safe reconciliation of existing games.

    Records name provenance for every active game and replaces a recognised
    placeholder with the best name a linked source already reported. Locked names
    get a review proposal instead. Nothing is created or deleted, and IDs,
    folders, Playnite GUIDs and tracking data are untouched. Placeholders with no
    known better name are reported in `unresolved` for the next metadata refresh."""
    query = (
        select(Game)
        .where(Game.user_id == user_id, Game.deleted_at.is_(None))
        .order_by(Game.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if limit:
        query = query.limit(limit)
    games = list((await db.scalars(query)).all())
    if dry_run:
        report = ReconcileReport(checked=len(games))
        for game in games:
            if is_weak(
                classify_title(game.title, source=game.source, external_id=game.external_id)
            ):
                name = await _best_source_name(db, user_id, game)
                if name is None:
                    report.unresolved.append(game.id)
                elif TITLE in (game.locked_fields or []):
                    report.proposed.append(game.id)
                else:
                    report.replaced.append(game.id)
        return report
    observed: dict[UUID, ObservedName] = {}
    unresolved: list[UUID] = []
    for game in games:
        if is_weak(classify_title(game.title, source=game.source, external_id=game.external_id)):
            name = await _best_source_name(db, user_id, game)
            if name is None:
                unresolved.append(game.id)
            else:
                observed[game.id] = name
    report = await reconcile_games(db, user_id, games, observed)
    report.unresolved = unresolved
    return report


# --- review ------------------------------------------------------------------


async def pending_reviews(db: AsyncSession, user_id: UUID) -> list[tuple[GameFieldValue, Game]]:
    rows = await db.execute(
        select(GameFieldValue, Game)
        .join(Game, (Game.id == GameFieldValue.game_id) & (Game.user_id == GameFieldValue.user_id))
        .where(
            GameFieldValue.user_id == user_id,
            GameFieldValue.field == TITLE,
            GameFieldValue.state == FieldValueState.PROPOSED.value,
            Game.deleted_at.is_(None),
        )
        .order_by(GameFieldValue.recorded_at.desc(), GameFieldValue.id)
    )
    return list(rows.tuples().all())


async def _proposal(
    db: AsyncSession, user_id: UUID, proposal_id: UUID
) -> tuple[GameFieldValue, Game]:
    row = (
        await db.execute(
            select(GameFieldValue, Game)
            .join(
                Game,
                (Game.id == GameFieldValue.game_id) & (Game.user_id == GameFieldValue.user_id),
            )
            .where(
                GameFieldValue.id == proposal_id,
                GameFieldValue.user_id == user_id,
                GameFieldValue.field == TITLE,
                GameFieldValue.state == FieldValueState.PROPOSED.value,
            )
            .with_for_update(of=GameFieldValue)
            .execution_options(populate_existing=True)
        )
    ).first()
    if row is None:
        raise LookupError("No open name change with that ID")
    return row[0], row[1]


async def accept_review(db: AsyncSession, user_id: UUID, proposal_id: UUID) -> Game:
    """Update: take the proposed name. A lock the user set stays in place."""
    proposal, game = await _proposal(db, user_id, proposal_id)
    stamp = _now()
    current = (await _current_values(db, user_id, [game.id])).get(game.id)
    await _track_current(db, game, current, None, stamp)
    await db.flush()
    current = (await _current_values(db, user_id, [game.id]))[game.id]
    _supersede(current, stamp)
    await db.flush()
    _rename(db, game, str(proposal.value), stamp)
    proposal.state = FieldValueState.CURRENT.value
    proposal.resolved_at = stamp
    proposal.locked = TITLE in (game.locked_fields or [])
    proposal.lock_origin = LockOrigin.USER.value if proposal.locked else None
    await db.flush()
    return game


async def keep_review(db: AsyncSession, user_id: UUID, proposal_id: UUID) -> Game:
    """Keep current: remember the answer so the same source name is not asked again."""
    proposal, game = await _proposal(db, user_id, proposal_id)
    proposal.state = FieldValueState.REJECTED.value
    proposal.resolved_at = _now()
    await db.flush()
    return game
