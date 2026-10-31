"""Bounded, provider-neutral game imports behind the live games.write grant."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from datetime import date
from typing import Annotated, Any
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.app_integrations import get_max_upload_size_mb
from src.database.models.game import Game
from src.features.game_identity.provider_ids import merge_provider_ids
from src.features.game_ingestion import legacy as ingestion
from src.features.game_ingestion.contract import SourceObservationV1
from src.features.imports.game_progression import apply_provider_progress
from src.features.imports.library_games import get_or_create_game
from src.features.metadata.locked_fields import apply_metadata_updates
from src.helpers.public_http import names_a_private_host, parse_http_url
from src.helpers.remote_images import RemoteImageError, download_image
from src.helpers.save_game_asset import AssetKind, asset_present, save_game_asset

ShortText = Annotated[str, Field(max_length=128)]
ProviderKey = Annotated[str, Field(pattern=r"^[a-z0-9._-]{1,50}$")]
ProviderIdentity = Annotated[str, Field(min_length=1, max_length=256)]
ArtworkUrl = Annotated[str, Field(min_length=1, max_length=2048)]

# Optional picture addresses (Plugin API 1.1.19) and the game asset each one fills.
ARTWORK_FIELDS: tuple[tuple[str, AssetKind], ...] = (
    ("key_art_url", "key_art"),
    ("banner_url", "banner"),
    ("logo_url", "logo"),
    ("icon_url", "icon"),
)
_ARTWORK_CONCURRENCY = 4
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# The same year bounds as the public metadata contract.
_EARLIEST_RELEASE, _LATEST_RELEASE = date(1800, 1, 1), date(3000, 12, 31)


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
    release_date: date | None = None
    key_art_url: ArtworkUrl | None = None
    banner_url: ArtworkUrl | None = None
    logo_url: ArtworkUrl | None = None
    icon_url: ArtworkUrl | None = None

    @field_validator("title", "external_id")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Use a nonblank title and external identity")
        return value

    @field_validator("release_date", mode="before")
    @classmethod
    def iso_release_date(cls, value: object) -> object:
        """JSON carries a YYYY-MM-DD string; strict mode would otherwise refuse every date."""
        if isinstance(value, str):
            if not _ISO_DATE.fullmatch(value):
                raise ValueError("release_date must be a YYYY-MM-DD date")
            return date.fromisoformat(value)
        return value

    @field_validator("release_date")
    @classmethod
    def plausible_release_date(cls, value: date | None) -> date | None:
        if value is not None and not _EARLIEST_RELEASE <= value <= _LATEST_RELEASE:
            raise ValueError("release_date must fall between the years 1800 and 3000")
        return value

    @field_validator(*(field for field, _ in ARTWORK_FIELDS))
    @classmethod
    def public_image_url(cls, value: str | None) -> str | None:
        """Only addresses the host's public image download would accept are stored for fetching."""
        if value is None:
            return None
        try:
            parts = parse_http_url(value, allowed_ports={80, 443})
        except ValueError as exc:
            raise ValueError(
                "Artwork URLs must be HTTP(S) on port 80 or 443, without credentials or fragment"
            ) from exc
        if names_a_private_host(parts.hostname or ""):
            raise ValueError("Artwork URLs must name a public host")
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
    await db.execute(
        text("SELECT pg_advisory_xact_lock(:key)"),
        {
            "key": int.from_bytes(
                hashlib.sha256(f"plugin-game-import:{user_id}".encode()).digest()[:8],
                "big",
                signed=True,
            )
        },
    )
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
    observations: list[tuple[UUID, SourceObservationV1 | None]] = []
    artwork: list[tuple[UUID, AssetKind, str, dict[str, Any]]] = []
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
                for field in ("description", "developer", "publisher", "tags", "release_date")
                if getattr(item, field) is not None
            }
            updates.update(title=item.title, sort_title=item.title.lower())
            if item.provider_ids is not None and "provider_ids" not in (game.locked_fields or []):
                # Saved ids win: a sync cannot re-point a game at another store record.
                updates["provider_ids"], _ = merge_provider_ids(
                    game.provider_ids or {}, item.provider_ids
                )
            if item.playtime_seconds is not None:
                updates["playtime_seconds"] = max(game.playtime_seconds, item.playtime_seconds)
            apply_metadata_updates(game, updates)
            apply_provider_progress(game, owned=True, playtime_seconds=item.playtime_seconds)
            game.stale_since = None
            artwork.extend(_missing_artwork(game, item, result))
        elif game.stale_since is None:
            game.stale_since = int(time.time())
        observations.append(
            (
                game.id,
                ingestion.build_observation(
                    item.external_id,
                    title=item.title,
                    provider_ids=item.provider_ids,
                    available=item.available,
                    playtime_seconds=item.playtime_seconds,
                    description=item.description,
                    developer=item.developer,
                    publisher=item.publisher,
                    tags=item.tags,
                    release_date=item.release_date,
                ),
            )
        )
        result.update(
            title=game.title,
            status=game.status.value,
            playtime_seconds=game.playtime_seconds,
            available=game.stale_since is None,
        )
        results.append(result)
    await ingestion.record_observations(
        db,
        user_id,
        ingestion.plugin_connection(plugin_id, batch.source_scope, batch.source_label),
        observations,
    )
    await db.commit()
    if artwork:
        await _save_missing_artwork(artwork, await get_max_upload_size_mb(db) * 1024 * 1024)
    return {"games": results}


def _missing_artwork(
    game: Game, item: ImportedGameInput, result: dict[str, Any]
) -> list[tuple[UUID, AssetKind, str, dict[str, Any]]]:
    """Artwork only fills pictures the game lacks; an existing picture (a user's
    upload or earlier provider artwork) is never replaced by a sync."""
    offered = ((getattr(item, field), kind) for field, kind in ARTWORK_FIELDS)
    return [
        (game.id, kind, url, result)
        for url, kind in offered
        if url is not None and not asset_present(game, kind)
    ]


async def _save_missing_artwork(
    artwork: list[tuple[UUID, AssetKind, str, dict[str, Any]]], max_bytes: int
) -> None:
    """Fetch after the import commits, outside its locks, through the public image boundary.

    The same bounded download (public hosts only, checked redirects, byte and pixel
    limits) and game-folder storage as library enrichment; the plugin never supplies
    bytes or paths. One failed picture is reported and never fails the import."""
    semaphore = asyncio.Semaphore(_ARTWORK_CONCURRENCY)

    async def save(game_id: UUID, kind: AssetKind, url: str, result: dict[str, Any]) -> None:
        async with semaphore:
            try:
                content = await asyncio.to_thread(download_image, url, max_bytes=max_bytes)
                await save_game_asset(content, game_id, kind, keep_existing=True)
            except (RemoteImageError, OSError, ValueError):
                result.setdefault("artwork_failed", []).append(kind)

    await asyncio.gather(*(save(*entry) for entry in artwork))
    order = [kind for _, kind in ARTWORK_FIELDS]
    for _, _, _, result in artwork:
        if "artwork_failed" in result:
            result["artwork_failed"].sort(key=order.index)
