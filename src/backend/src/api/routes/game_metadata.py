"""Game metadata routes, composed by the games router."""

import asyncio
from datetime import date
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    status,
)
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.routes.settings import (
    get_or_create_scan_settings,
)
from src.api.routes.utils.games import (
    _DATA_ROOT,
    _get_game_or_404,
    _record_field_changes,
    _record_provider_use,
    _scan_settings_to_preferences,
)
from src.core.app_integrations import get_max_upload_size_mb
from src.core.auth import get_current_user
from src.core.preferences import load_preferences
from src.core.titles import normalize_metadata_title as _normalize_metadata_title
from src.database.models.game import GameLink
from src.database.models.user import User
from src.database.session import get_db
from src.features.achievement_catalogue import (
    fetch_catalogue,
    has_catalogue,
    store_catalogue,
)
from src.features.game_identity import names
from src.features.game_identity.provider_ids import (
    find_provider_id_conflicts,
    merge_provider_ids,
)
from src.features.metadata.locked_fields import apply_metadata_updates
from src.features.metadata.service import (
    game_result,
    library_candidate,
    resolve_library_record,
    search_games,
)
from src.features.provider_play import provider_custom_values, provider_play_compatibility
from src.helpers.remote_images import MAX_BYTES, download_image
from src.helpers.save_game_asset import (
    ASSET_FILENAMES,
    save_locked_game_asset,
)
from src.plugin_api.metadata_contracts import MediaType

_QUERY_DEFAULT = Query(..., min_length=2, max_length=100)


_LIMIT_DEFAULT = Query(default=8, ge=1, le=20)


_INCLUDE_IMAGES_DEFAULT = Query(default=True)


_DB_DEPENDENCY = Depends(get_db)


_CURRENT_USER_DEPENDENCY = Depends(get_current_user)


router = APIRouter()
search_router = APIRouter()


class MetadataSearchResponse(BaseModel):
    __module__ = "src.api.routes.games"
    query: str
    providers: list[str]
    steamgriddb_configured: bool = False
    provider_errors: list[str] = []
    results: list[dict]


class MetadataRefreshRequest(BaseModel):
    __module__ = "src.api.routes.games"
    dry_run: bool = False
    update_text: bool = True
    fill_missing_art: bool = True
    overwrite_existing_art: bool = False
    expected_updated_at: int | None = None


# Parallel routes/models intentionally share this shape.
# pylint: disable=duplicate-code
@search_router.get("/metadata/search", response_model=MetadataSearchResponse)
async def search_metadata(
    query: str = _QUERY_DEFAULT,
    limit: int = _LIMIT_DEFAULT,
    include_images: bool = _INCLUDE_IMAGES_DEFAULT,
    db: AsyncSession = _DB_DEPENDENCY,
    current_user: User = _CURRENT_USER_DEPENDENCY,
) -> dict:
    """Search external providers for data that can prefill a new game.

    Keys come from the user's own settings first, then the server-wide ones
    (Server Integrations or the environment). Provider order and which
    fields get saved come from the user's scan settings.
    """
    scan_settings = await get_or_create_scan_settings(current_user.id, db)
    preferences = _scan_settings_to_preferences(scan_settings)
    preferences["steam_user_tags"] = (await load_preferences(db, current_user.id))[
        "steam_user_tags"
    ]
    # Compatibility clients share search, but cannot trigger media without selection.
    del include_images
    result = await search_games(db, current_user.id, query.strip(), limit, preferences=preferences)

    if result.get("used_provider_ids"):
        _record_provider_use(scan_settings, result["used_provider_ids"])
        await db.commit()
    return result


# pylint: enable=duplicate-code


# Metadata updates and history tracking intentionally overlap in their declared fields.
# pylint: disable=duplicate-code
_GAME_METADATA_FIELDS = frozenset(
    {
        "title",
        "description",
        "developer",
        "publisher",
        "series",
        "tags",
        "features",
        "age_rating",
        "release_date",
        "time_to_beat_hours",
        "time_to_beat_extras_hours",
        "time_to_beat_completionist_hours",
        "time_to_beat_all_hours",
        "links",
    }
)
# pylint: enable=duplicate-code


def _metadata_value_is_present(value: object) -> bool:
    return value is not None and value != "" and value != []


def _game_links(raw: object) -> list[GameLink] | None:
    """Provider links as rows the relationship accepts, clipped to the column sizes."""
    if not isinstance(raw, list):
        return None
    rows = [
        GameLink(label=str(link["label"])[:50], url=str(link["url"]))
        for link in raw
        if isinstance(link, dict)
        and link.get("label")
        and 0 < len(str(link.get("url", ""))) <= 2048
    ]
    return rows or None


def _release_date(raw: object) -> date | None:
    if isinstance(raw, date):
        return raw
    try:
        return date.fromisoformat(str(raw)[:10]) if raw else None
    except ValueError:
        return None


# Keep the established workflow and public parameters together.
# pylint: disable=too-many-branches,too-many-locals,too-many-statements
@router.post("/{game_id}/metadata/refresh")
async def refresh_game_metadata(
    game_id: UUID,
    payload: MetadataRefreshRequest,
    db: AsyncSession = _DB_DEPENDENCY,
    current_user: User = _CURRENT_USER_DEPENDENCY,
) -> dict:
    """Preview or apply a metadata-provider refresh for one game.

    Provider lookup uses the same configured game metadata registry as the
    existing search flow. Applying is server-side so authorization, locks,
    history, artwork protection, and stale-editor detection cannot be bypassed
    by a client replaying a provider result.
    """
    game = await _get_game_or_404(game_id, db, current_user.id)
    scan_settings = await get_or_create_scan_settings(current_user.id, db)
    preferences = _scan_settings_to_preferences(scan_settings)
    record, failures = await resolve_library_record(
        db,
        current_user.id,
        library_candidate(
            game.title,
            MediaType.GAME,
            game.provider_ids or {},
            game.release_date.year if game.release_date else None,
        ),
        include_media=payload.fill_missing_art or payload.overwrite_existing_art,
        preferences=preferences,
    )
    result = {
        "providers": record.get("providers", []),
        "provider_errors": failures,
        "results": [game_result(record)] if record else [],
    }

    provider_errors = result.get("provider_errors", [])
    _record_provider_use(scan_settings, record.get("contributors", []))

    match = next(
        (
            candidate
            for candidate in result.get("results", [])
            if isinstance(candidate, dict)
            and _normalize_metadata_title(str(candidate.get("title") or ""))
            == _normalize_metadata_title(game.title)
        ),
        None,
    )
    if match is None:
        await db.commit()
        return {
            "status": "no-match",
            "provider": None,
            "provider_errors": provider_errors,
            "changed_fields": [],
            "skipped_locked_fields": [],
            "would_add_key_art": False,
            "would_add_banner": False,
            "game_updated_at": game.updated_at,
        }

    max_image_bytes = MAX_BYTES
    if payload.fill_missing_art and not payload.dry_run:
        # The limits singleton can initialize/commit. Resolve it before taking
        # the game lock so a first-use setup cannot release storage mid-write.
        max_image_bytes = (await get_max_upload_size_mb(db)) * 1024 * 1024

    if not payload.dry_run:
        # Provider lookup can overlap an editor or sync. Check the latest owned row
        # and its protection under the same lock used to apply the refresh.
        game = await _get_game_or_404(
            game_id, db, current_user.id, for_update=True, for_storage=True
        )
        if (
            payload.expected_updated_at is not None
            and game.updated_at != payload.expected_updated_at
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Game changed after the metadata preview. Refresh the editor and try again.",
            )
    else:
        game = await _get_game_or_404(game_id, db, current_user.id, for_storage=True)

    field_values: dict[str, object] = {
        "title": match.get("title"),
        "description": match.get("description"),
        "developer": match.get("developer"),
        "publisher": match.get("publisher"),
        "series": match.get("series"),
        "tags": match.get("tags"),
        "features": match.get("features"),
        "age_rating": match.get("age_rating"),
        "release_date": _release_date(match.get("release_date")),
        "time_to_beat_hours": match.get("time_to_beat_hours"),
        "time_to_beat_extras_hours": match.get("time_to_beat_extras_hours"),
        "time_to_beat_completionist_hours": match.get("time_to_beat_completionist_hours"),
        "time_to_beat_all_hours": match.get("time_to_beat_all_hours"),
        "links": _game_links(match.get("links")),
    }
    gated_flags = {
        "developer": "save_developer",
        "publisher": "save_publisher",
        "series": "save_series",
        "tags": "save_tags",
        "features": "save_features",
        "description": "save_description",
        "age_rating": "save_age_rating",
        "release_date": "save_release_date",
        "time_to_beat_hours": "save_time_to_beat",
        "time_to_beat_extras_hours": "save_time_to_beat",
        "time_to_beat_completionist_hours": "save_time_to_beat",
        "time_to_beat_all_hours": "save_time_to_beat",
    }
    updates: dict[str, object] = {}
    skipped_locked: list[str] = []
    for field, fresh in field_values.items():
        if (
            field != "title"
            and gated_flags.get(field)
            and not preferences.get(gated_flags[field], True)
        ):
            continue
        if not _metadata_value_is_present(fresh):
            continue
        if field in game.locked_fields:
            skipped_locked.append(field)
            continue
        current = getattr(game, field)
        if field == "links" and isinstance(fresh, list):
            if [(row.label, row.url) for row in fresh] != [(row.label, row.url) for row in current]:
                updates[field] = fresh
        elif fresh != current:
            updates[field] = fresh

    provider_play = provider_play_compatibility(match, game.play_compatibility)
    if provider_play is not None:
        if "play_compatibility" in game.locked_fields:
            skipped_locked.append("play_compatibility")
        else:
            updates["play_compatibility"] = provider_play

    provider_custom = provider_custom_values(match, game.provider_custom)
    if provider_custom is not None:
        if "provider_custom" in game.locked_fields:
            skipped_locked.append("provider_custom")
        else:
            updates["provider_custom"] = provider_custom

    existing_provider_ids = game.provider_ids or {}
    provider_ids_to_save = existing_provider_ids
    if "provider_ids" in game.locked_fields:
        skipped_locked.append("provider_ids")
    else:
        offered_provider_ids = match.get("provider_ids", {})
        provider_ids_to_add = {
            namespace: identity
            for namespace, identity in offered_provider_ids.items()
            if namespace not in existing_provider_ids
        }
        _, replaced_namespaces = merge_provider_ids(existing_provider_ids, offered_provider_ids)
        for namespace in replaced_namespaces:
            provider_errors.append(
                f"{match.get('provider', 'Metadata')} returned a different "
                f"{namespace} ID; the saved identity was kept."
            )

        identity_conflicts = await find_provider_id_conflicts(
            db, current_user.id, game.id, provider_ids_to_add
        )
        conflicting_namespaces = {namespace for namespace, _identity, _id in identity_conflicts}
        for namespace in sorted(conflicting_namespaces):
            provider_errors.append(
                f"{match.get('provider', 'Metadata')} returned a {namespace} ID "
                "already linked to another game; it was skipped."
            )
        safe_incoming = {
            namespace: identity
            for namespace, identity in offered_provider_ids.items()
            if namespace not in conflicting_namespaces
        }
        provider_ids_to_save, _replaced = merge_provider_ids(existing_provider_ids, safe_incoming)

    changed_fields = sorted(updates)
    key_art_url = match.get("key_art_url")
    banner_url = match.get("banner_url")
    game_dir = _DATA_ROOT / str(game.user_id) / "games" / game.folder_location
    key_art_exists = (game_dir / ASSET_FILENAMES["key_art"]).is_file()
    banner_exists = (game_dir / ASSET_FILENAMES["banner"]).is_file()
    would_add_key_art = bool(
        payload.fill_missing_art
        and key_art_url
        and (payload.overwrite_existing_art or not key_art_exists)
    )
    would_add_banner = bool(
        payload.fill_missing_art
        and banner_url
        and (payload.overwrite_existing_art or not banner_exists)
    )

    if payload.dry_run:
        # A preview records no game changes, but the existing provider-last-used
        # telemetry may be updated as part of the provider lookup.
        await db.commit()
        return {
            "status": "preview",
            "provider": match.get("provider"),
            "provider_errors": provider_errors,
            "changed_fields": changed_fields,
            "skipped_locked_fields": sorted(set(skipped_locked)),
            "would_add_key_art": would_add_key_art,
            "would_add_banner": would_add_banner,
            "game_updated_at": game.updated_at,
        }

    game.provider_ids = provider_ids_to_save
    if payload.update_text:
        _record_field_changes(game, updates, db)
        apply_metadata_updates(game, updates)
        if "title" in updates:
            names.note_title_change(game, trusted_metadata=True)
            await names.reconcile_games(db, game.user_id, [game])

    if payload.fill_missing_art:

        async def _download_art(url: str) -> bytes:
            return await asyncio.to_thread(download_image, url, max_bytes=max_image_bytes)

        if key_art_url and (payload.overwrite_existing_art or not key_art_exists):
            try:
                await save_locked_game_asset(await _download_art(str(key_art_url)), game, "key_art")
            except Exception as exc:  # pylint: disable=broad-exception-caught
                provider_errors.append(
                    f"{match.get('provider', 'Metadata')}: cover art could not be downloaded: {exc}"
                )
            else:
                would_add_key_art = True
        if banner_url and (payload.overwrite_existing_art or not banner_exists):
            try:
                await save_locked_game_asset(await _download_art(str(banner_url)), game, "banner")
            except Exception as exc:  # pylint: disable=broad-exception-caught
                provider_errors.append(
                    f"{match.get('provider', 'Metadata')}: banner art could not be downloaded: {exc}"
                )
            else:
                would_add_banner = True

    if payload.update_text and not await has_catalogue(db, game.id):
        # A game with no achievements yet may get its definitions from a provider (such as Atlas).
        try:
            label, catalogue, _errors = await fetch_catalogue(db, current_user.id, game)
            if catalogue:
                await store_catalogue(db, game.id, label, catalogue)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            provider_errors.append(f"Achievements: {type(exc).__name__}")

    await db.commit()
    await db.refresh(game)
    return {
        "status": "updated",
        "provider": match.get("provider"),
        "provider_errors": provider_errors,
        "changed_fields": changed_fields,
        "skipped_locked_fields": sorted(set(skipped_locked)),
        "would_add_key_art": would_add_key_art,
        "would_add_banner": would_add_banner,
        "game_updated_at": game.updated_at,
    }


# pylint: enable=too-many-branches,too-many-locals,too-many-statements
