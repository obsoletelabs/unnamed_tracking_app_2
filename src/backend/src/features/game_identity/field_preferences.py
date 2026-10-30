"""Which source a person trusts for one metadata field of one game (#349).

A preference is not a lock: a lock freezes the value, a preference only decides whose value
wins when sources disagree. On a refresh the order is lock > preference > the existing
"take the fresh value" rule, and a preferred source that has nothing to offer never blanks a
field (the refresh then fills it as usual).

A source is a metadata provider (`provider:<id>`, known from the values a refresh recorded in
`game_field_values`) or another copy in the same umbrella (`copy:<game id>`).
"""

from __future__ import annotations

import time
from collections.abc import Collection
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

PROVIDER_PREFIX = "provider:"
COPY_PREFIX = "copy:"
_LABEL_LENGTH = 64


class InvalidPreference(ValueError):
    """The field or source key is not one a preference can name."""


def provider_key(provider: object) -> str | None:
    """The source key for a metadata provider name, or None when it has no usable name."""
    label = str(provider or "").strip()[:_LABEL_LENGTH]
    return f"{PROVIDER_PREFIX}{label}" if label else None


def is_present(value: Any) -> bool:
    return value is not None and value != "" and value != []


def _from_stored(field: str, value: Any) -> Any:
    if field == "release_date" and isinstance(value, str):
        return date.fromisoformat(value)
    return value


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
                [FieldValueState.CURRENT.value, FieldValueState.SUPERSEDED.value]
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
    for (field, label), value in sorted((await _provider_values(db, game, wanted)).items()):
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
        current = field_provenance.stored(getattr(game, field, None))
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
                return field_provenance.stored(value) if is_present(value) else None
        raise InvalidPreference("That copy is not linked to this game.")
    if source_key.startswith(PROVIDER_PREFIX):
        label = source_key[len(PROVIDER_PREFIX) :]
        if not label or len(label) > _LABEL_LENGTH or label != label.strip():
            raise InvalidPreference("Invalid provider source.")
        value = (await _provider_values(db, game, [field])).get((field, label))
        return value if is_present(value) else None
    raise InvalidPreference("A source key starts with provider: or copy:.")


async def set_preference(db: AsyncSession, game: Game, field: str, source_key: str) -> bool:
    """Prefer `source_key` for `field`; returns whether the game's value changed now.

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
    if value is None or field in (game.locked_fields or []):
        return False
    new_value = _from_stored(field, value)
    if new_value == getattr(game, field, None):
        return False
    setattr(game, field, new_value)
    label = source_key[len(PROVIDER_PREFIX) :] if source_key.startswith(PROVIDER_PREFIX) else None
    await field_provenance.record_field_sources(
        db, game, {field: new_value}, origin=FieldValueOrigin.METADATA, provider=label
    )
    return True


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
    db: AsyncSession, game: Game, provider: object, fields: Collection[str]
) -> set[str]:
    """Fields of `fields` a refresh from `provider` must leave alone.

    That is each field whose preferred source is a different one that reports a value; a
    preferred source with no value for the field is ignored so the refresh can fill it."""
    chosen = {field: key for field, key in (await preferences(db, game)).items() if field in fields}
    own = provider_key(provider)
    held: set[str] = set()
    for field, key in chosen.items():
        if key == own:
            continue
        try:
            value = await _validated_source(db, game, field, key)
        except InvalidPreference:
            continue  # the copy was unlinked: nothing to defer to
        if value is not None:
            held.add(field)
    return held
