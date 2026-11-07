"""Which source a person trusts for one metadata field of one game (#349).

A preference is not a lock: a lock freezes the value, a preference only decides whose value
wins when sources disagree. On a refresh the order is lock > preference > the existing
"take the fresh value" rule, and a preferred source that has nothing to offer never blanks a
field (the refresh then fills it as usual).

A source is a metadata provider (`provider:<id>`, known from the values a refresh recorded in
`game_field_values`) or another copy in the same umbrella (`copy:<game id>`).

A refresh keeps every reporting provider's value, also one that lost the merge (a `candidate`,
D2), so preferring a lower-priority provider takes its value at once when it is known, and a
refresh takes the preferred provider's fresh value instead of the merged one.
"""

from __future__ import annotations

import time
from collections.abc import Collection, Mapping
from datetime import date
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models.game import Game
from src.database.models.game_identity import GameIdentity
from src.database.models.game_identity_tracking import (
    FieldValueOrigin,
    FieldValueState,
    GameFieldPreference,
    GameFieldValue,
)
from src.features.game_identity import field_provenance
from src.features.game_identity.field_provenance import PROVENANCE_FIELDS
from src.features.metadata.game_releases import release_of

PROVIDER_PREFIX = "provider:"
COPY_PREFIX = "copy:"
_LABEL_LENGTH = 64
# Before per-field sources (#348) a refresh recorded every field under the merged record's lead,
# "Library", so the only provider a person could prefer was this one. It always meant "whatever
# the refresh merges"; it is kept meaning that, never a lock on an old value.
LEGACY_MERGED_LABEL = "Library"
LEGACY_MERGED_SOURCE = f"{PROVIDER_PREFIX}{LEGACY_MERGED_LABEL}"

# What `set_preference` did with the game's value (`applied` is the only one that changed it):
# `unchanged` the game already holds the source's value, `locked` the field is locked, and
# `awaiting_value` the source has no known value yet (the next refresh that reports one applies it).
APPLIED = "applied"
UNCHANGED = "unchanged"
LOCKED = "locked"
AWAITING_VALUE = "awaiting_value"


class InvalidPreference(ValueError):
    """The field or source key is not one a preference can name."""


def provider_key(provider: object) -> str | None:
    """The source key for a metadata provider name, or None when it has no usable name."""
    label = str(provider or "").strip()[:_LABEL_LENGTH]
    return f"{PROVIDER_PREFIX}{label}" if label else None


def is_present(value: Any) -> bool:
    return value is not None and value != "" and value != []


def _from_stored(field: str, value: Any) -> tuple[Any, str | None]:
    """A recorded value as the game stores it, with the release date's precision (a year
    ``"2011"`` or a month ``"2011-04"`` is the first day of that period, C5.4)."""
    if field == "release_date" and isinstance(value, str):
        if len(value) == 4:
            return date(int(value), 1, 1), "year"
        if len(value) == 7:
            return date.fromisoformat(f"{value}-01"), "month"
        return date.fromisoformat(value), None
    return value, None


async def _copies(db: AsyncSession, game: Game) -> list[Game]:
    """The other live copies in the umbrella that holds `game`."""
    umbrella_id = await db.scalar(
        select(GameIdentity.umbrella_id).where(
            GameIdentity.user_id == game.user_id, GameIdentity.game_id == game.id
        )
    )
    if umbrella_id is None:
        return []
    return list(
        await db.scalars(
            select(Game)
            .join(
                GameIdentity,
                (GameIdentity.user_id == Game.user_id) & (GameIdentity.game_id == Game.id),
            )
            .where(
                GameIdentity.umbrella_id == umbrella_id,
                Game.user_id == game.user_id,
                Game.id != game.id,
                Game.deleted_at.is_(None),
            )
            .order_by(GameIdentity.position, Game.id)
        )
    )


async def _provider_values(
    db: AsyncSession, game: Game, fields: Collection[str]
) -> dict[tuple[str, str], Any]:
    """The newest value each provider recorded for each field, keyed by (field, provider)."""
    rows = await db.scalars(
        field_provenance.field_values_of(game, fields)
        .where(
            GameFieldValue.origin == FieldValueOrigin.METADATA.value,
            GameFieldValue.source_label.is_not(None),
            GameFieldValue.state.in_(
                [
                    FieldValueState.CURRENT.value,
                    FieldValueState.SUPERSEDED.value,
                    # a value that lost a refresh's merge (D2)
                    FieldValueState.CANDIDATE.value,
                ]
            ),
        )
        .order_by(GameFieldValue.recorded_at, GameFieldValue.id)
    )
    return {(row.field, str(row.source_label)): row.value for row in rows}


async def preferences(db: AsyncSession, game: Game) -> dict[str, str]:
    """field -> source key for a game."""
    rows = await db.scalars(
        select(GameFieldPreference).where(
            GameFieldPreference.user_id == game.user_id, GameFieldPreference.game_id == game.id
        )
    )
    return {row.field: row.source_key for row in rows}


async def source_values(
    db: AsyncSession, game: Game, fields: Collection[str] | None = None
) -> dict[str, list[dict[str, Any]]]:
    """Per field, the value each known source reports, with the current one marked."""
    wanted = sorted(PROVENANCE_FIELDS if fields is None else PROVENANCE_FIELDS & set(fields))
    out: dict[str, list[dict[str, Any]]] = {field: [] for field in wanted}
    reported = await _provider_values(db, game, wanted)
    # the legacy merged label is offered only while no real provider has a value for the field
    named = {field for field, label in reported if label != LEGACY_MERGED_LABEL}
    for (field, label), value in sorted(reported.items()):
        if label == LEGACY_MERGED_LABEL and field in named:
            continue
        if is_present(value):
            out[field].append(
                {
                    "source_key": f"{PROVIDER_PREFIX}{label}",
                    "kind": "provider",
                    "label": label,
                    "value": value,
                }
            )
    for copy in await _copies(db, game):
        for field in wanted:
            value = getattr(copy, field, None)
            if is_present(value):
                out[field].append(
                    {
                        "source_key": f"{COPY_PREFIX}{copy.id}",
                        "kind": "copy",
                        "label": copy.title,
                        "value": field_provenance.stored(value),
                    }
                )
    for field, entries in out.items():
        current = field_provenance.stored_field(game, field, getattr(game, field, None))
        for entry in entries:
            entry["current"] = entry["value"] == current
    return out


async def _validated_source(db: AsyncSession, game: Game, field: str, source_key: str) -> Any:
    """The value `source_key` reports for `field` (None when it has none); raises if invalid."""
    if field not in PROVENANCE_FIELDS:
        raise InvalidPreference(f"Unknown metadata field: {field}")
    if source_key.startswith(COPY_PREFIX):
        try:
            copy_id = UUID(source_key[len(COPY_PREFIX) :])
        except ValueError as exc:
            raise InvalidPreference("Invalid copy source.") from exc
        for copy in await _copies(db, game):
            if copy.id == copy_id:
                value = getattr(copy, field, None)
                return (
                    field_provenance.stored_field(copy, field, value) if is_present(value) else None
                )
        raise InvalidPreference("That copy is not linked to this game.")
    if source_key.startswith(PROVIDER_PREFIX):
        label = source_key[len(PROVIDER_PREFIX) :]
        if not label or len(label) > _LABEL_LENGTH or label != label.strip():
            raise InvalidPreference("Invalid provider source.")
        value = (await _provider_values(db, game, [field])).get((field, label))
        return value if is_present(value) else None
    raise InvalidPreference("A source key starts with provider: or copy:.")


async def set_preference(db: AsyncSession, game: Game, field: str, source_key: str) -> str:
    """Prefer `source_key` for `field`; returns what happened to the game's value now
    (`APPLIED`, `UNCHANGED`, `LOCKED` or `AWAITING_VALUE`).

    The preferred source's known value is taken at once unless the field is locked."""
    value = await _validated_source(db, game, field, source_key)
    row = await db.scalar(
        select(GameFieldPreference).where(
            GameFieldPreference.game_id == game.id, GameFieldPreference.field == field
        )
    )
    if row is None:
        db.add(
            GameFieldPreference(
                user_id=game.user_id,
                game_id=game.id,
                field=field,
                source_key=source_key,
                created_at=int(time.time()),
            )
        )
    else:
        row.source_key = source_key
    if field in (game.locked_fields or []):
        return LOCKED
    if value is None:
        return AWAITING_VALUE
    new_value, precision = _from_stored(field, value)
    if field == "release_date":
        if (new_value, precision) == (game.release_date, game.release_date_precision):
            return UNCHANGED
        game.release_date = new_value
        # after the date: a changed date resets the precision (see `Game`)
        game.release_date_precision = precision
    elif new_value == getattr(game, field, None):
        return UNCHANGED
    else:
        setattr(game, field, new_value)
    label = source_key[len(PROVIDER_PREFIX) :] if source_key.startswith(PROVIDER_PREFIX) else None
    await field_provenance.record_field_sources(
        db, game, {field: new_value}, origin=FieldValueOrigin.METADATA, provider=label
    )
    return APPLIED


_CUT = {"year": 4, "month": 7}


def _release_value(reported: Mapping[str, Any]) -> str | None:
    """A provider's own release date as provenance stores it: ``"2011"``, ``"2011-04"`` or a
    day, from its ``first_release`` or its legacy day (`release_of`)."""
    day, precision = release_of(
        {
            "first_release": reported.get("first_release"),
            "release_date": reported.get("release_date"),
        }
    )
    if day is None:
        return None
    return day.isoformat()[: _CUT.get(precision or "", 10)]


def provider_candidates(
    match: Mapping[str, Any], fields: Collection[str], recorded: Mapping[str, object]
) -> dict[str, dict[str, Any]]:
    """field -> {provider label: stored value} each provider of a refresh result reported for
    `fields`, leaving out the provider whose value the game now records (`recorded`: field ->
    label). These are kept as candidates (D2)."""
    out: dict[str, dict[str, Any]] = {}
    wanted = PROVENANCE_FIELDS & set(fields)
    for label, reported in (match.get("provider_values") or {}).items():
        if not isinstance(reported, Mapping) or not str(label).strip():
            continue
        for field in wanted:
            if str(recorded.get(field) or "") == label:
                continue
            raw = _release_value(reported) if field == "release_date" else reported.get(field)
            if is_present(raw):
                out.setdefault(field, {})[label] = field_provenance.stored(raw)
    return out


def with_preferred_values(match: dict[str, Any], chosen: Mapping[str, str]) -> dict[str, Any]:
    """`match` where each field preferred to a provider that reported it this time holds that
    provider's value and names it in `field_providers`, also when its value lost the merge.

    The release date takes the provider's own ``first_release`` and legacy day together, so
    the precision follows. A copy, the legacy merged label or a provider that reported nothing
    for the field leaves the merged value."""
    values = match.get("provider_values") or {}
    field_providers = dict(match.get("field_providers") or {})
    out = dict(match)
    for field, key in chosen.items():
        if field not in PROVENANCE_FIELDS or not key.startswith(PROVIDER_PREFIX):
            continue
        if key == LEGACY_MERGED_SOURCE:
            continue
        label = key[len(PROVIDER_PREFIX) :]
        reported = values.get(label)
        if not isinstance(reported, Mapping):
            continue
        if field == "release_date":
            if _release_value(reported) is None:
                continue
            out["first_release"] = reported.get("first_release")
            out["release_date"] = reported.get("release_date")
            field_providers["first_release"] = field_providers["release_date"] = label
        elif is_present(reported.get(field)):
            out[field] = reported[field]
            field_providers[field] = label
    out["field_providers"] = field_providers
    return out


async def clear_preference(db: AsyncSession, game: Game, field: str) -> bool:
    """Remove the preference for `field`; returns whether one existed."""
    if field not in PROVENANCE_FIELDS:
        raise InvalidPreference(f"Unknown metadata field: {field}")
    row = await db.scalar(
        select(GameFieldPreference).where(
            GameFieldPreference.game_id == game.id, GameFieldPreference.field == field
        )
    )
    if row is None:
        return False
    await db.delete(row)
    return True


async def fields_held_by_other_source(
    db: AsyncSession,
    game: Game,
    provider: object,
    fields: Collection[str],
    *,
    field_providers: Mapping[str, object] | None = None,
) -> set[str]:
    """Fields of `fields` a refresh from `provider` must leave alone.

    That is each field whose preferred source is a different one that reports a value; a
    preferred source with no value for the field is ignored so the refresh can fill it. A
    merged result names, in `field_providers`, the provider each field's value came from."""
    chosen = {field: key for field, key in (await preferences(db, game)).items() if field in fields}
    held: set[str] = set()
    for field, key in chosen.items():
        if key in (
            LEGACY_MERGED_SOURCE,
            provider_key((field_providers or {}).get(field) or provider),
        ):
            continue
        try:
            value = await _validated_source(db, game, field, key)
        except InvalidPreference:
            continue  # the copy was unlinked: nothing to defer to
        if value is not None:
            held.add(field)
    return held
