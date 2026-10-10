"""Bounded, provider-neutral game imports behind the live games.write grant."""

from __future__ import annotations

import hashlib
import json
import time
from typing import Annotated, Any
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models.game import Game, GameStatus
from src.features.imports.library_games import get_or_create_game
from src.features.metadata.locked_fields import apply_metadata_updates

ShortText = Annotated[str, Field(max_length=128)]
ProviderKey = Annotated[str, Field(pattern=r"^[a-z0-9._-]{1,50}$")]
ProviderIdentity = Annotated[str, Field(min_length=1, max_length=256)]


class ImportedGameInput(BaseModel):
    """Remote identity, optional metadata and reported availability/playtime."""

    model_config = ConfigDict(extra="forbid", strict=True)
    external_id: str = Field(min_length=1, max_length=256)
    title: str = Field(min_length=1, max_length=500)
    description: str | None = Field(default=None, max_length=20000)
    developer: str | None = Field(default=None, max_length=200)
    publisher: str | None = Field(default=None, max_length=200)
    tags: list[ShortText] | None = Field(default=None, max_length=50)
    provider_ids: dict[ProviderKey, ProviderIdentity] | None = Field(default=None, max_length=32)
    playtime_seconds: int | None = Field(default=None, ge=0, le=2_000_000_000)
    available: bool = True

    @field_validator("title", "external_id")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Use a nonblank title and external identity")
        return value


class GameImportInput(BaseModel):
    """One source scope and at most 25 individually identified games."""

    model_config = ConfigDict(extra="forbid", strict=True)
    source_label: str = Field(min_length=1, max_length=50)
    source_scope: str = Field(min_length=1, max_length=256)
    items: list[ImportedGameInput] = Field(min_length=1, max_length=25)

    @field_validator("source_label", "source_scope")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Use a nonblank source label and scope")
        return value

    @field_validator("items")
    @classmethod
    def distinct_identities(cls, items: list[ImportedGameInput]) -> list[ImportedGameInput]:
        if len({item.external_id for item in items}) != len(items):
            raise ValueError("Each batch item must have a distinct external identity")
        return items


def game_import_identity(plugin_id: str, user_id: UUID, scope: str, external_id: str) -> UUID:
    """Titles and display labels may change without changing the imported row."""
    return uuid5(
        NAMESPACE_URL,
        json.dumps(
            ["plugin-game-v1", plugin_id, str(user_id), scope, external_id], separators=(",", ":")
        ),
    )


async def dispatch_game_import(
    db: AsyncSession, *, plugin_id: str, user_id: UUID, payload: dict[str, Any]
) -> dict[str, Any]:
    """Validate before writes; retries cannot adopt unrelated rows or restore Trash."""
    batch = GameImportInput.model_validate(payload)
    identities = {
        item.external_id: game_import_identity(
            plugin_id, user_id, batch.source_scope, item.external_id
        )
        for item in batch.items
    }
    # Serialize only this user's plugin import transactions, never provider HTTP.
    key = int.from_bytes(
        hashlib.sha256(f"plugin-game-import:{user_id}".encode()).digest()[:8], "big", signed=True
    )
    await db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})
    existing = {
        game.id: game
        for game in await db.scalars(
            select(Game)
            .where(Game.id.in_(identities.values()), Game.user_id == user_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    }
    results = []
    for item in batch.items:
        identity = identities[item.external_id]
        game = existing.get(identity)
        result: dict[str, Any] = {
            "external_id": item.external_id,
            "id": str(identity),
            "created": False,
        }
        if game is not None and (
            game.deleted_at is not None or game.external_id != item.external_id
        ):
            result["conflict"] = "deleted" if game.deleted_at is not None else "identity_changed"
            results.append(result)
            continue
        if game is None and not item.available:
            result.update(id=None, skipped="not_imported")
            results.append(result)
            continue
        if game is None:
            game, result["created"] = await get_or_create_game(
                db, user_id, item.title, batch.source_label, item.external_id, identity=identity
            )
        if item.available:
            updates: dict[str, Any] = {
                field: getattr(item, field)
                for field in ("description", "developer", "publisher", "tags")
                if getattr(item, field) is not None
            }
            updates.update(title=item.title, sort_title=item.title.lower())
            if item.provider_ids is not None:
                updates["provider_ids"] = {**(game.provider_ids or {}), **item.provider_ids}
            if item.playtime_seconds is not None:
                updates["playtime_seconds"] = max(game.playtime_seconds, item.playtime_seconds)
            apply_metadata_updates(game, updates)
            if result["created"] and game.playtime_seconds > 0:
                game.status = GameStatus.PLAYED
            game.stale_since = None
        elif game.stale_since is None:
            game.stale_since = int(time.time())
        result.update(
            title=game.title,
            status=game.status.value,
            playtime_seconds=game.playtime_seconds,
            available=game.stale_since is None,
        )
        results.append(result)
    await db.commit()
    return {"games": results}
