"""Game metadata routes, composed by the games router."""

import asyncio
import time
from typing import Annotated
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    status,
)
from pydantic import BaseModel, Field
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
from src.database.models.game import Game, GameLink
from src.database.models.game_field_change import GameFieldChange
from src.database.models.game_identity_tracking import FieldValueOrigin
from src.database.models.user import User
from src.database.session import get_db
from src.features.achievement_catalogue import (
    fetch_catalogue,
    has_catalogue,
    store_catalogue,
)
from src.features.game_identity import field_preferences, field_provenance, names, refresh_match
from src.features.game_identity.ingest_relations import ingest_relations, record_merged_ids
from src.features.game_identity.provider_ids import (
    find_provider_id_conflicts,
    merge_provider_ids,
)
from src.features.metadata.game_releases import add_release_updates, release_for
from src.features.metadata.locked_fields import apply_metadata_updates
from src.features.metadata.service import (
    field_source,
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


_Namespace = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[a-z0-9][a-z0-9._-]*$")]
_Identity = Annotated[str, Field(min_length=1, max_length=512)]


class MetadataRefreshRequest(BaseModel):
    __module__ = "src.api.routes.games"
    dry_run: bool = False
    update_text: bool = True
    fill_missing_art: bool = True
    overwrite_existing_art: bool = False
    expected_updated_at: int | None = None
    # A result that matches only by title (no reported provider id is one the game holds) is
    # applied only when the person confirms it after a preview (CONTRACTS-v1 C4.4 rule 3). The
    # confirmation is the previewed `candidate.provider_ids`: the apply looks the game up again,
    # and a candidate with any other ids is answered `needs-confirmation`, never applied.
    confirm_candidate: dict[_Namespace, _Identity] | None = Field(default=None, max_length=32)


class MetadataRejectionRequest(BaseModel):
    __module__ = "src.api.routes.games"
    # the `candidate.provider_ids` of a refresh response the person says is not this game
    provider_ids: dict[_Namespace, _Identity] = Field(min_length=1, max_length=32)


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


@router.get("/{game_id}/field-sources")
async def get_field_sources(
    game_id: UUID,
    db: AsyncSession = _DB_DEPENDENCY,
    current_user: User = _CURRENT_USER_DEPENDENCY,
) -> dict[str, dict]:
    """Who supplied the current value of each recorded metadata field."""
    game = await _get_game_or_404(game_id, db, current_user.id)
    seen = await field_provenance.field_sources(db, current_user.id, game_id)
    chosen = await field_preferences.preferences(db, game)
    reported = await field_preferences.source_values(db, game)
    return {
        field: {
            **seen.get(field, {}),
            "preferred": chosen.get(field),
            "sources": reported.get(field, []),
        }
        for field in field_provenance.PROVENANCE_FIELDS
        if field in seen or field in chosen or reported.get(field)
    }


class FieldPreferenceRequest(BaseModel):
    __module__ = "src.api.routes.games"
    source_key: str = Field(min_length=1, max_length=100)


@router.put("/{game_id}/field-preferences/{field}")
async def set_field_preference(
    game_id: UUID,
    field: str,
    payload: FieldPreferenceRequest,
    db: AsyncSession = _DB_DEPENDENCY,
    current_user: User = _CURRENT_USER_DEPENDENCY,
) -> dict:
    """Prefer one source for a metadata field; its known value is taken now unless locked.

    `outcome` says why `applied` is false: `unchanged` (the game already holds that value),
    `locked`, or `awaiting_value` (the source has reported no value yet; the next refresh that
    reports one applies it, also when that value loses the merge to another provider)."""
    game = await _get_game_or_404(game_id, db, current_user.id, for_update=True)
    try:
        outcome = await field_preferences.set_preference(db, game, field, payload.source_key)
    except field_preferences.InvalidPreference as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    await db.commit()
    return {
        "field": field,
        "source_key": payload.source_key,
        "applied": outcome == field_preferences.APPLIED,
        "outcome": outcome,
    }


@router.delete("/{game_id}/field-preferences/{field}", status_code=status.HTTP_204_NO_CONTENT)
async def clear_field_preference(
    game_id: UUID,
    field: str,
    db: AsyncSession = _DB_DEPENDENCY,
    current_user: User = _CURRENT_USER_DEPENDENCY,
) -> None:
    """Stop preferring a source for a metadata field. The value stays as it is."""
    game = await _get_game_or_404(game_id, db, current_user.id, for_update=True)
    try:
        await field_preferences.clear_preference(db, game, field)
    except field_preferences.InvalidPreference as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    await db.commit()


async def _follow_moved_ids(
    db: AsyncSession,
    game: Game,
    decision: refresh_match.RefreshMatch,
    saved: dict[str, str],
    provider_errors: list[str],
) -> tuple[dict[str, str], dict[str, str]]:
    """The saved ids with every id a provider moved (C4.4 rule 2) replaced by the live one, and
    the moves another game blocks (namespace -> live id), which go to the duplicate review."""
    if not decision.followed:
        return saved, {}
    moved = {namespace: move["to"] for namespace, move in decision.followed.items()}
    blocked: dict[str, str] = {}
    taken = await find_provider_id_conflicts(db, game.user_id, game.id, moved)
    for namespace, live, _other in taken:
        provider_errors.append(
            f"The {namespace} ID moved to {live}, which another game already holds; the saved "
            "ID was kept. Review duplicates to merge the two games."
        )
        moved.pop(namespace, None)
        blocked[namespace] = live
    return {**saved, **moved}, blocked


def _record_followed_ids(
    db: AsyncSession,
    game: Game,
    decision: refresh_match.RefreshMatch,
    saved: dict[str, str],
    to_save: dict[str, str],
) -> None:
    """Keep each saved id a rule 2 rewrite replaced in the game's history, so the id the host
    held can be restored if the provider later detaches a wrong merge (C4.2)."""
    now = int(time.time())
    for namespace, move in sorted(decision.followed.items()):
        if saved.get(namespace) != move["from"] or to_save.get(namespace) != move["to"]:
            continue
        db.add(
            GameFieldChange(
                game_id=game.id,
                field_name="provider_ids",
                old_value=f"{namespace}: {move['from']}",
                new_value=f"{namespace}: {move['to']} (moved by {move['provider']})",
                changed_at=now,
            )
        )


def _by_source(updates: dict[str, object], sources: dict[str, str | None]) -> dict:
    """The updated fields grouped by the provider that supplied them."""
    grouped: dict[str | None, list[str]] = {}
    for field in updates:
        if field in sources:
            grouped.setdefault(sources[field], []).append(field)
    return grouped


def _reported_title(record: dict) -> str | None:
    """The title a provider reported, never the saved title the lookup was started with."""
    title = (record.get("metadata") or {}).get("title")
    if not title and record.get("provider") != "local":
        # a search candidate (title-only lookup) carries the provider's own title
        title = record.get("title")
    return str(title) if title else None


def _refresh_outcome(
    status_name: str,
    game: Game,
    provider_errors: list[str],
    decision: refresh_match.RefreshMatch | None = None,
    details: dict[str, object] | None = None,
) -> dict:
    """One response shape for every refresh outcome; the C4.4 fields are additive."""
    return {
        "status": status_name,
        "provider": None,
        "provider_errors": provider_errors,
        "changed_fields": [],
        "skipped_locked_fields": [],
        "would_add_key_art": False,
        "would_add_banner": False,
        "game_updated_at": game.updated_at,
        # "id" (a reported id the game holds), "title" (title only: apply needs
        # `confirm_candidate`), "conflict", "rejected" or "none"
        "match": decision.tier if decision else "none",
        "candidate": None,
        "conflicts": decision.conflict_candidates() if decision else [],
        "rejected_ids": dict(decision.rejected) if decision else {},
        # ids a provider moved (a merge or redirect, Plugin API 1.1.20): {ns: {from, to, provider}}
        "followed_ids": dict(decision.followed) if decision else {},
        **(details or {}),
    }


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

    match = result["results"][0] if result["results"] else None
    claims = record.get("provider_id_claims", {}) if record else {}
    reported_title = _reported_title(record) if record else None
    if match is None or not (claims or reported_title):
        await db.commit()
        return _refresh_outcome("no-match", game, provider_errors)

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

    # Decided on the row just read (locked when applying), so ids an editor changed during the
    # provider lookup are the ones compared. Titles never gate an id match (C4.4).
    decision = refresh_match.classify(
        game.provider_ids or {},
        claims,
        game_title=game.title,
        reported_title=reported_title,
        rejected=await refresh_match.rejected_ids(db, game.id),
        identities=record.get("provider_identities") or {},
    )
    candidate = {"title": reported_title, "provider_ids": decision.offered}
    if decision.tier in {"none", "conflict", "rejected"}:
        await db.commit()
        return _refresh_outcome(
            {"none": "no-match"}.get(decision.tier, decision.tier),
            game,
            provider_errors,
            decision,
            {"candidate": candidate, "provider": match.get("provider")},
        )
    # A title-only match is previewed; it is applied only once the person confirms this very
    # candidate. The lookup above ran again, so a different candidate with the same title (a
    # remake, a re-ranked provider) is offered for confirmation instead of being saved.
    needs_confirmation = (
        decision.tier == "title"
        and not payload.dry_run
        and payload.confirm_candidate != decision.offered
    )
    if needs_confirmation and payload.confirm_candidate is not None:
        provider_errors.append(
            "The title-only match changed after the preview; confirm the new candidate to apply it."
        )
    preview_only = payload.dry_run or needs_confirmation
    # A field preferred to a provider that reported it takes that provider's value, also one
    # that lost the merge to a higher-priority provider (D2).
    match = field_preferences.with_preferred_values(
        match, await field_preferences.preferences(db, game)
    )

    field_values: dict[str, object] = {
        "title": match.get("title"),
        "description": match.get("description"),
        "developer": match.get("developer"),
        "publisher": match.get("publisher"),
        "series": match.get("series"),
        "tags": match.get("tags"),
        "features": match.get("features"),
        "age_rating": match.get("age_rating"),
        "release_date": release_for(game, match)[0],
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
    # fields the refresh offered with the value the game already holds (their source is re-read)
    unchanged: dict[str, object] = {}
    skipped_locked: list[str] = []
    # Lock > preference: a field whose preferred source is another one with a value is left alone.
    # Each field of the merged result names the provider it came from (Steam, IGDB, Atlas...).
    sources = {field: field_source(match, field) for field in field_values}
    held_by_preference = await field_preferences.fields_held_by_other_source(
        db, game, match.get("provider"), field_values.keys(), field_providers=sources
    )
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
        if field in held_by_preference:
            continue
        current = getattr(game, field)
        if field == "links" and isinstance(fresh, list):
            if [(row.label, row.url) for row in fresh] != [(row.label, row.url) for row in current]:
                updates[field] = fresh
        elif fresh != current:
            updates[field] = fresh
        else:
            unchanged[field] = current

    add_release_updates(
        game,
        match,
        updates,
        skipped_locked,
        save_release_date=preferences.get("save_release_date", True),
        held_by_preference=held_by_preference,
    )

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
    # None: the provider_ids lock kept rule 2 from being considered, so the review is unchanged
    merged_ids: dict[str, str] | None = None
    if "provider_ids" in game.locked_fields:
        skipped_locked.append("provider_ids")
    else:
        # rule 2: a provider moved a saved id (a merge or redirect); follow it unless another
        # game already holds the live id, which the duplicate review is for
        existing_provider_ids, merged_ids = await _follow_moved_ids(
            db, game, decision, existing_provider_ids, provider_errors
        )
        # only ids the sources reported themselves; the record also echoes the request's ids
        offered_provider_ids = decision.offered
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

    details: dict[str, object] = {
        "candidate": candidate,
        "provider": match.get("provider"),
        "changed_fields": changed_fields,
        "skipped_locked_fields": sorted(set(skipped_locked)),
        "would_add_key_art": would_add_key_art,
        "would_add_banner": would_add_banner,
    }
    if preview_only:
        # A preview records no game changes, but the existing provider-last-used
        # telemetry may be updated as part of the provider lookup.
        await db.commit()
        return _refresh_outcome(
            "needs-confirmation" if needs_confirmation else "preview",
            game,
            provider_errors,
            decision,
            details,
        )

    _record_followed_ids(db, game, decision, game.provider_ids or {}, provider_ids_to_save)
    game.provider_ids = provider_ids_to_save
    if merged_ids is not None:
        # games whose provider ids merged are flagged through the duplicate review (C4.4)
        await record_merged_ids(db, game, merged_ids)
    relations = {"added": 0, "pending": 0}
    if payload.update_text:
        _record_field_changes(game, updates, db)
        apply_metadata_updates(game, updates)
        for provider, fields in _by_source(updates, sources).items():
            await field_provenance.record_field_sources(
                db,
                game,
                {field: updates[field] for field in fields},
                origin=FieldValueOrigin.METADATA,
                provider=provider,
            )
        # an unchanged value recorded under the old merged "Library" label is re-attributed
        for provider, fields in _by_source(unchanged, sources).items():
            await field_provenance.relabel_unchanged(
                db, game, {field: unchanged[field] for field in fields}, provider=provider
            )
        # what every other provider reported (values that lost the merge), so a preference
        # can choose one of them later (D2); fields the person does not save are left alone
        saved_fields = {
            field
            for field in field_values
            if not (gated_flags.get(field) and not preferences.get(gated_flags[field], True))
        }
        recorded = {field: sources.get(field) for field in {*updates, *unchanged}}
        await field_provenance.record_candidates(
            db,
            game,
            field_preferences.provider_candidates(match, saved_fields, recorded),
            fields=saved_fields,
            reporting=(match.get("provider_values") or {}).keys(),
        )
        if "title" in updates:
            names.note_title_change(game, trusted_metadata=True)
            await names.reconcile_games(db, game.user_id, [game])
        # Plugin API 1.1.20 game relations; a savepoint, so a failure keeps the metadata update
        try:
            async with db.begin_nested():
                relations = await ingest_relations(db, game, record.get("provider_relations") or [])
        except Exception as exc:  # pylint: disable=broad-exception-caught
            provider_errors.append(f"Relations: {type(exc).__name__}")

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
                # a savepoint: a failed catalogue store must not undo the metadata update
                async with db.begin_nested():
                    await store_catalogue(db, game.id, label, catalogue)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            provider_errors.append(f"Achievements: {type(exc).__name__}")

    await db.commit()
    await db.refresh(game)
    return _refresh_outcome(
        "updated",
        game,
        provider_errors,
        decision,
        {
            **details,
            "would_add_key_art": would_add_key_art,
            "would_add_banner": would_add_banner,
            "relations_added": relations["added"],
            "relations_pending": relations["pending"],
        },
    )


# pylint: enable=too-many-branches,too-many-locals,too-many-statements


@router.get("/{game_id}/metadata/rejections")
async def list_metadata_rejections(
    game_id: UUID,
    db: AsyncSession = _DB_DEPENDENCY,
    current_user: User = _CURRENT_USER_DEPENDENCY,
) -> dict:
    """Provider ids the person said are not this game; a refresh never adopts them."""
    game = await _get_game_or_404(game_id, db, current_user.id)
    rejected = await refresh_match.rejected_ids(db, game.id)
    return {
        "rejected": [
            {"namespace": namespace, "identity": identity}
            for namespace, identity in sorted(rejected)
        ]
    }


@router.post("/{game_id}/metadata/rejections")
async def reject_metadata_candidate(
    game_id: UUID,
    payload: MetadataRejectionRequest,
    db: AsyncSession = _DB_DEPENDENCY,
    current_user: User = _CURRENT_USER_DEPENDENCY,
) -> dict:
    """Remember that a refresh candidate is not this game, so later refreshes refuse it."""
    game = await _get_game_or_404(game_id, db, current_user.id, for_update=True)
    stored = await refresh_match.reject(db, game, payload.provider_ids)
    if not stored:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "Every id in this candidate is already saved on the game; "
                "edit the game's provider ids instead."
            ),
        )
    await db.commit()
    return {"rejected": [{"namespace": ns, "identity": value} for ns, value in stored.items()]}


_NAMESPACE_QUERY = Query(default=None, min_length=1, max_length=128)
_IDENTITY_QUERY = Query(default=None, min_length=1, max_length=512)


@router.delete("/{game_id}/metadata/rejections", status_code=status.HTTP_204_NO_CONTENT)
async def forget_metadata_rejections(
    game_id: UUID,
    namespace: str | None = _NAMESPACE_QUERY,
    identity: str | None = _IDENTITY_QUERY,
    db: AsyncSession = _DB_DEPENDENCY,
    current_user: User = _CURRENT_USER_DEPENDENCY,
) -> None:
    """Forget one rejection (`?namespace=&identity=`), or every rejection for this game.

    The next refresh may match the forgotten ids again."""
    if (namespace is None) != (identity is None):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Give both namespace and identity to forget one rejection, or neither.",
        )
    game = await _get_game_or_404(game_id, db, current_user.id, for_update=True)
    only = (namespace, identity) if namespace is not None and identity is not None else None
    await refresh_match.forget_rejections(db, game.id, only=only)
    await db.commit()
