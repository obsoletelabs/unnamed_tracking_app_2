"""API routes for managing games, notes, and game artwork."""

import asyncio
import time
from uuid import UUID

from fastapi import (
    APIRouter,
    Body,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Response,
    status,
)
from sqlalchemy import and_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.functions import count as sql_count

from src.api.routes import (
    game_assets,
    game_checklists,
    game_files,
    game_metadata,
    game_notes,
    game_profiles,
)
from src.api.routes.game_assets import (
    ASSET_FILENAMES,
    AssetUrlRequest,
    DetectDatesRequest,
    MediaItemUpdate,
    _media_item_to_dict,
    _thumb_dir,
    delete_game_screenshot,
    detect_media_dates,
    download_game_asset,
    get_clip_thumbnail,
    get_game_asset,
    get_game_screenshot,
    list_game_screenshot_trash,
    list_game_screenshots,
    restore_game_screenshot,
    save_clip_thumbnail,
    update_media_item,
    upload_game_asset,
    upload_game_screenshots,
)
from src.api.routes.game_checklists import (
    ChecklistItemUpdate,
    ChecklistItemWrite,
    ChecklistReorder,
    _checklist_item_to_dict,
    create_checklist_item,
    delete_checklist_item,
    list_game_checklist,
    reorder_checklist,
    update_checklist_item,
)
from src.api.routes.game_files import (
    GameFileUpdate,
    _game_file_subdir,
    _game_file_to_dict,
    _sync_game_file_items,
    delete_game_file,
    get_game_file,
    list_game_file_trash,
    list_game_files,
    restore_game_file,
    update_game_file,
    upload_game_files,
)
from src.api.routes.game_metadata import (
    _GAME_METADATA_FIELDS,
    MetadataRefreshRequest,
    MetadataSearchResponse,
    _metadata_value_is_present,
    _normalize_metadata_title,
    refresh_game_metadata,
    search_metadata,
)
from src.api.routes.game_notes import (
    NoteRename,
    NoteWrite,
    create_game_note,
    delete_game_note,
    get_game_note,
    list_game_note_summaries,
    list_game_notes,
    rename_game_note,
    update_game_note,
)
from src.api.routes.game_profiles import (
    ProfileUpdate,
    ProfileWrite,
    WiseOldManSyncRequest,
    _get_profile_or_404,
    _profile_to_dict,
    _record_stat_snapshot,
    create_game_profile,
    delete_game_profile,
    get_profile_stat_history,
    list_game_profile_trash,
    list_game_profiles,
    restore_game_profile,
    sync_profile_wiseoldman,
    update_game_profile,
)
from src.api.routes.media_common import title_search
from src.api.routes.utils.games import (
    _DATA_ROOT,
    _NON_NULLABLE_UPDATE_FIELDS,
    _NOTE_NAME_PATTERN,
    ALLOWED_ASSET_KINDS,
    FIELD_CHANGE_TRACKED_FIELDS,
    _derive_sort_title,
    _drop_nulls_for_required_fields,
    _duplicate_folder_error,
    _ensure_folder_location_available,
    _field_change_value_to_text,
    _game_note_path,
    _get_game_or_404,
    _normalize_note_name,
    _note_summary,
    _record_field_changes,
    _scan_settings_to_preferences,
)
from src.api.schemas.game import (
    CollectionIndexGame,
    GameBulkUpdate,
    GameCreate,
    GameFieldChangeRead,
    GameRead,
    GameUpdate,
)
from src.core.auth import AuthenticatedActor, app_grant, get_current_actor, get_current_user
from src.database.models.game import Game, GameLink, GameStatus
from src.database.models.game_field_change import GameFieldChange
from src.database.models.user import User
from src.database.session import get_db
from src.features import game_folders, game_ownership
from src.features.achievement_sync_queue import enqueue_game as enqueue_achievement_sync
from src.features.game_identity import manual
from src.features.game_identity.provider_ids import find_provider_id_conflicts
from src.features.game_ingestion import playnite
from src.features.game_ingestion.legacy import (
    has_manual_observation,
    record_manual_fields,
    record_playnite_fields,
)
from src.features.metadata.locked_fields import apply_updates_with_locking, authorize_title_update
from src.features.trash.game_trash import move_game_to_trash, restore_game_from_trash
from src.features.trash.sweep import RETENTION_SECONDS
from src.helpers.save_game_asset import (
    create_game_folder,
)

_QUERY_DEFAULT = Query(..., min_length=2, max_length=100)
_LIMIT_DEFAULT = Query(default=8, ge=1, le=20)
_INCLUDE_IMAGES_DEFAULT = Query(default=True)
_FILES_DEFAULT = File(None, alias="files")
_FILE_DEFAULT = File(None, alias="file")
_UNSCOPED_ONLY_DEFAULT = Query(False, description="Only items with no profile_id set.")
_FAVORITE_DEFAULT = Query(default=None)
_SEARCH_DEFAULT = Query(default=None, description="Case-insensitive title search")
_SKIP_DEFAULT = Query(default=0, ge=0)
_LIMIT_DEFAULT_2 = Query(default=50, ge=1, le=1000)

router = APIRouter(
    prefix="/api/game",
    tags=["game"],
    dependencies=[Depends(get_current_user)],
)


_DB_DEPENDENCY = Depends(get_db)
_CURRENT_USER_DEPENDENCY = Depends(get_current_user)
# one instance per field name: FastAPI names a File() after the first
# parameter it is used on, so sharing one made `files` demand a field "file"
_FILE_UPLOAD = File(...)
_FILES_UPLOAD = File(...)
_BODY_DOTDOTDOT = Body(...)
_NONE_FORM = Form(None)
_MODIFIED_FORM = Form(None)
_FILE_MODIFIED_FORM = Form(None)
_NONE_QUERY_STATUS = Query(default=None, alias="status")


router.include_router(game_metadata.search_router)


router.include_router(game_assets.asset_read_router)


# the same set a metadata search/refresh is allowed to overwrite (see the
# scan-settings save_* toggles in ScanSettingsSection.vue). A field outside
# this set (folder_location, favorite, playtime, ...) isn't "metadata" in
# that sense, so history only tracks what a provider could plausibly have
# changed underneath the user


# columns a PATCH can't blank: an explicit null for one of these means "no
# change", not "clear it" (the database would reject the NULL anyway, which
# used to surface as a misleading duplicate-folder error)


router.include_router(game_assets.router)


router.include_router(game_files.router)


router.include_router(game_notes.crud_router)


router.include_router(game_profiles.router)


router.include_router(game_checklists.router)


async def _find_playnite_game(payload: GameCreate, user_id: UUID, db: AsyncSession) -> Game | None:
    """The active game a Playnite create should reconcile onto, if any: the
    one already carrying this GUID, else the one in the requested folder when
    it isn't claimed by a different Playnite entry (then it's a genuine
    folder conflict and the caller reports it). A folder match without a
    GUID is adopted by recording the GUID on it."""
    active = (Game.user_id == user_id, Game.deleted_at.is_(None))
    by_guid = await db.scalar(
        select(Game).where(*active, Game.playnite_guid == payload.playnite_guid).limit(1)
    )
    if by_guid is not None:
        return by_guid
    by_folder = await db.scalar(
        select(Game).where(*active, Game.folder_location == payload.folder_location)
    )
    if by_folder is None or by_folder.playnite_guid not in (None, payload.playnite_guid):
        return None
    if by_folder.playnite_guid is None:
        by_folder.playnite_guid = payload.playnite_guid
        await db.commit()
        await db.refresh(by_folder)
    return by_folder


async def _reject_trashed_playnite_game(guid: UUID, user_id: UUID, db: AsyncSession) -> None:
    """A GUID whose game sits in Trash is reported, not silently recreated.

    Creating a second row for the same Playnite entry would split one source
    record across two identities. Restore the game or let the sweep purge it."""
    trashed = await db.scalar(
        select(Game.id)
        .where(Game.user_id == user_id, Game.playnite_guid == guid, Game.deleted_at.is_not(None))
        .limit(1)
    )
    if trashed is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "playnite_game_in_trash",
                "game_id": str(trashed),
                "message": "This Playnite game is in Trash. Restore it instead of creating it again.",
            },
        )


async def _validate_game_relationship(
    parent_game_id: UUID | None,
    relationship_type: str | None,
    db: AsyncSession,
    user_id: UUID,
    game_id: UUID | None = None,
) -> None:
    """A relationship_type only makes sense alongside a parent_game_id
    (GameUpdate can't enforce this itself — a partial update might set only
    one of the two fields in a given request, with the other already
    correct from an earlier one). Also blocks a game being its own parent
    and pointing at a parent that isn't actually this user's."""
    if relationship_type is not None and parent_game_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="relationship_type requires parent_game_id to be set.",
        )
    if parent_game_id is None:
        return
    await game_ownership.lock_changes(db, user_id)
    if game_id is not None and parent_game_id == game_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="A game cannot be its own parent."
        )
    parent = await db.scalar(
        select(Game).where(
            Game.id == parent_game_id, Game.user_id == user_id, Game.deleted_at.is_(None)
        )
    )
    if parent is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="parent_game_id does not exist."
        )
    try:
        await game_ownership.validate_relationship(db, user_id, parent, relationship_type, game_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


async def _ensure_provider_ids_available(
    db: AsyncSession,
    user_id: UUID,
    excluded_game_id: UUID | None,
    provider_ids: dict[str, str] | None,
    existing_provider_ids: dict[str, str] | None = None,
) -> None:
    """Reject provider identities already claimed by another active game."""
    if not provider_ids:
        return
    candidates = {
        namespace: identity
        for namespace, identity in provider_ids.items()
        if existing_provider_ids is None or existing_provider_ids.get(namespace) != identity
    }
    conflicts = await find_provider_id_conflicts(db, user_id, excluded_game_id, candidates)
    if conflicts:
        namespace = conflicts[0][0]
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "provider_id_conflict",
                "field": "provider_ids",
                "provider": namespace,
                "message": (
                    f"This {namespace} ID is already linked to another game in your library."
                ),
            },
        )


@router.post(
    "/create",
    response_model=GameRead,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_409_CONFLICT: {
            "description": "Duplicate folder_location",
            "content": {
                "application/json": {
                    "example": {
                        "detail": {
                            "error": "duplicate_folder_location",
                            "field": "folder_location",
                            "value": "ExistingFolder",
                            "message": "A game with folder_location 'ExistingFolder' already exists.",
                        }
                    }
                }
            },
        }
    },
)
@app_grant("library.sync")
async def create_game(
    payload: GameCreate,
    response: Response,
    db: AsyncSession = _DB_DEPENDENCY,
    current_user: User = _CURRENT_USER_DEPENDENCY,
) -> Game:
    """Create a game after validating its folder location.

    A create that carries a `playnite_guid` is idempotent: if this user
    already has that Playnite game (by GUID, or by folder name with no other
    GUID claiming it) the existing game is returned with 200 instead of a
    duplicate-folder failure, so a repeated or concurrent Playnite sync
    reconciles rather than erroring (#184). A manual create (no GUID) still
    gets 409 for a folder name that's taken."""
    sent = payload.model_dump(exclude_unset=True)
    # A rollback expires ORM attributes; retain the owner ID for the create-race retry.
    user_id = current_user.id
    if payload.playnite_guid is not None:
        # Keep GUID and folder preflight atomic with competing authenticated syncs.
        # A folder check can otherwise reject a winner created after the GUID lookup.
        await game_ownership.lock_changes(db, user_id)
        existing = await _find_playnite_game(payload, user_id, db)
        if existing is not None:
            await record_playnite_fields(
                db, current_user.id, existing.id, payload.playnite_guid, sent
            )
            await db.commit()
            response.status_code = status.HTTP_200_OK
            return existing
        await _reject_trashed_playnite_game(payload.playnite_guid, current_user.id, db)

    await _ensure_provider_ids_available(db, current_user.id, None, payload.provider_ids)

    await _ensure_folder_location_available(payload.folder_location, current_user.id, db)
    try:
        game_folders.ensure_name_available(_DATA_ROOT, current_user.id, payload.folder_location)
    except game_folders.FolderStorageError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await _validate_game_relationship(
        payload.parent_game_id, payload.relationship_type, db, current_user.id
    )

    data = payload.model_dump()
    data["user_id"] = current_user.id
    if not data.get("sort_title"):
        data["sort_title"] = _derive_sort_title(data["title"])
    # an explicit None would bypass the column default and violate NOT NULL
    if data.get("created_at") is None:
        data.pop("created_at", None)

    # `links` is a relationship, not a plain column — the constructor needs
    # actual GameLink instances, not the raw {label, url} dicts model_dump
    # produces
    link_rows = [GameLink(label=link["label"], url=link["url"]) for link in data.pop("links", [])]

    game = Game(**data)
    game.links = link_rows
    db.add(game)

    try:
        await db.flush()
        create_game_folder(game.user_id, game.folder_location)
        # Evidence about the new record, keyed by its stable source ID: the
        # Playnite GUID, or this entry's own ID for a manual game.
        if payload.playnite_guid is not None:
            await record_playnite_fields(db, current_user.id, game.id, payload.playnite_guid, sent)
        else:
            await record_manual_fields(db, current_user.id, game.id, sent)
        await enqueue_achievement_sync(db, user_id, game.id)
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        # another sync created the same Playnite game between the check
        # above and this insert: hand back the row that won the race
        if payload.playnite_guid is not None:
            existing = await _find_playnite_game(payload, user_id, db)
            if existing is not None:
                await record_playnite_fields(db, user_id, existing.id, payload.playnite_guid, sent)
                await db.commit()
                response.status_code = status.HTTP_200_OK
                return existing
        raise _duplicate_folder_error(payload.folder_location) from exc

    return game


# Keep the established workflow and public parameters together.
# pylint: disable=too-many-positional-arguments
@router.get("/list", response_model=list[GameRead])
@app_grant("library.sync")
async def list_games(
    response: Response,
    db: AsyncSession = _DB_DEPENDENCY,
    current_user: User = _CURRENT_USER_DEPENDENCY,
    status_filter: GameStatus | None = _NONE_QUERY_STATUS,
    favorite: bool | None = _FAVORITE_DEFAULT,
    search: str | None = _SEARCH_DEFAULT,
    skip: int = _SKIP_DEFAULT,
    limit: int = _LIMIT_DEFAULT_2,
    include_counts: bool = Query(default=True),
) -> list[Game]:
    """Return one page of games filtered by status, favorite flag, or title search.

    The body stays a plain array. `X-Total-Count` carries the number of games
    matching the filters so a client can request the remaining pages together.
    """
    # Keep the global search/favorite scope separate from the selected status:
    # the page can be narrowed to one tab while its count headers still report
    # the matching totals for every status tab.
    base_predicates = [Game.user_id == current_user.id, Game.deleted_at.is_(None)]
    if favorite is not None:
        base_predicates.append(Game.favorite == favorite)
    title_match = title_search([Game.title], search)
    if title_match is not None:
        base_predicates.append(title_match)

    predicates = list(base_predicates)
    if status_filter is not None:
        predicates.append(Game.status == status_filter)

    # Count the selected status and all status tabs in one aggregate query;
    # then fetch only the requested page. This keeps browser-visible filtering
    # to one HTTP request while preserving accurate tab totals.
    page_stmt = select(Game).where(*predicates).order_by(Game.sort_title).offset(skip).limit(limit)
    if include_counts:
        total_count = sql_count(Game.id)
        if status_filter is not None:
            total_count = total_count.filter(Game.status == status_filter)
        status_columns = [
            sql_count(Game.id)
            .filter(Game.status == game_status)
            .label(f"status_{game_status.value}")
            for game_status in GameStatus
        ]
        count_subquery = (
            select(total_count.label("total_count"), *status_columns)
            .where(*base_predicates)
            .subquery()
        )
        status_count_columns = [
            count_subquery.c[f"status_{game_status.value}"] for game_status in GameStatus
        ]
        result = await db.execute(
            select(Game, count_subquery.c.total_count, *status_count_columns)
            .select_from(count_subquery.outerjoin(Game, and_(*predicates)))
            .order_by(Game.sort_title)
            .offset(skip)
            .limit(limit)
        )
        rows = result.all()
        games = [row[0] for row in rows if row[0] is not None]
        counts = rows[0] if rows else None
        total = int(counts[1]) if counts is not None else 0
        response.headers["X-Total-Count"] = str(total)
        status_counts = {
            game_status.value.lower().replace("_", " "): int(counts[index + 2])
            for index, game_status in enumerate(GameStatus)
        }
        response.headers["X-Status-Counts"] = ",".join(
            f"{game_status}:{value}" for game_status, value in status_counts.items()
        )
    else:
        games = list((await db.scalars(page_stmt)).all())
    # Default covers are generated by the artwork route. Tell the browser
    # which rows have real stored art so missing-art rows can use the local
    # styled placeholder without one authenticated request per game.
    artwork_paths = [
        _DATA_ROOT / str(game.user_id) / "games" / game.folder_location / ASSET_FILENAMES["key_art"]
        for game in games
    ]
    artwork_presence = await asyncio.gather(
        *(asyncio.to_thread(path.is_file) for path in artwork_paths)
    )
    for game, has_key_art in zip(games, artwork_presence, strict=True):
        game.has_key_art = has_key_art
    return games


# pylint: enable=too-many-positional-arguments


@router.get("/collection-index", response_model=list[CollectionIndexGame])
@app_grant("library.sync")
async def collection_index(
    db: AsyncSession = _DB_DEPENDENCY,
    current_user: User = _CURRENT_USER_DEPENDENCY,
) -> list[CollectionIndexGame]:
    """Return the small projection used by manual and local smart collections.

    Membership rules live in the browser, so the overview still needs one
    record per game. Selecting these columns avoids sending descriptions,
    links, provider metadata, notes, and other detail-page fields.
    """
    result = await db.execute(
        select(
            Game.id,
            Game.title,
            Game.status,
            Game.favorite,
            Game.source,
            Game.tags,
            Game.collections,
            Game.playtime_seconds,
        )
        .where(Game.user_id == current_user.id, Game.deleted_at.is_(None))
        .order_by(Game.sort_title, Game.id)
    )
    return [CollectionIndexGame.model_validate(row._mapping) for row in result]


@router.get("/get/{game_id}", response_model=GameRead)
async def get_game(
    game_id: UUID,
    db: AsyncSession = _DB_DEPENDENCY,
    current_user: User = _CURRENT_USER_DEPENDENCY,
) -> Game:
    """Return one game by ID."""
    return await _get_game_or_404(game_id, db, current_user.id)


@router.get("/{game_id}/variants", response_model=list[GameRead])
async def get_game_variants(
    game_id: UUID,
    db: AsyncSession = _DB_DEPENDENCY,
    current_user: User = _CURRENT_USER_DEPENDENCY,
) -> list[Game]:
    """Every game whose parent_game_id points at this one — the reverse of
    the parent breadcrumb (GameDetail.vue's `parentGameTitle`). A base game
    like Minecraft has no idea its modpacks exist otherwise, since the FK
    only points child -> parent."""
    await _get_game_or_404(game_id, db, current_user.id)
    stmt = (
        select(Game)
        .where(
            Game.user_id == current_user.id,
            Game.parent_game_id == game_id,
            Game.deleted_at.is_(None),
        )
        .order_by(Game.sort_title)
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


@router.patch(
    "/update/{game_id}",
    response_model=GameRead,
    responses={
        status.HTTP_409_CONFLICT: {
            "description": "Duplicate folder_location",
            "content": {
                "application/json": {
                    "example": {
                        "detail": {
                            "error": "duplicate_folder_location",
                            "field": "folder_location",
                            "value": "ExistingFolder",
                            "message": "A game with folder_location 'ExistingFolder' already exists.",
                        }
                    }
                }
            },
        }
    },
)
@app_grant("library.sync")
async def update_game(
    game_id: UUID,
    payload: GameUpdate,
    db: AsyncSession = _DB_DEPENDENCY,
    current_user: User = _CURRENT_USER_DEPENDENCY,
    actor: AuthenticatedActor = Depends(get_current_actor),
) -> Game:
    """Update a game and keep its derived sort title synchronized."""
    if payload.model_fields_set & {"parent_game_id", "relationship_type"}:
        await game_ownership.lock_changes(db, current_user.id)
    game = await _get_game_or_404(game_id, db, current_user.id, for_update=True)

    sent = payload.model_dump(exclude_unset=True)
    updates = _drop_nulls_for_required_fields(dict(sent))
    kept_locks = playnite.guard_update(game, updates, actor)
    authorize_title_update(game, updates, actor)
    old_folder = game.folder_location
    manual_status = manual.guard_status_update(
        game, updates, automation=getattr(actor, "credential_kind", None) == "api_key"
    )

    if "folder_location" in updates and updates["folder_location"] is not None:
        await _ensure_folder_location_available(
            updates["folder_location"], current_user.id, db, exclude_game_id=game_id
        )

    await _ensure_provider_ids_available(
        db,
        current_user.id,
        game_id,
        updates.get("provider_ids"),
        game.provider_ids,
    )

    if "parent_game_id" in updates or "relationship_type" in updates:
        effective_parent = updates.get("parent_game_id", game.parent_game_id)
        await _validate_game_relationship(
            effective_parent,
            updates.get("relationship_type", game.relationship_type),
            db,
            current_user.id,
            game_id,
        )

    if "folder_location" in updates:
        try:
            game_folders.recover_folder_move(_DATA_ROOT, game)
        except (game_folders.FolderStorageError, OSError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    # `links` is a relationship, not a plain column — setattr needs actual
    # GameLink instances, not the raw {label, url} dicts model_dump
    # produces. Reassigning the whole list lets cascade="all, delete-orphan"
    # (see Game.links) drop whichever rows aren't in the new list.
    if "links" in updates:
        new_links = updates.pop("links") or []
        game.links = [GameLink(label=link["label"], url=link["url"]) for link in new_links]

    _record_field_changes(game, updates, db)
    apply_updates_with_locking(game, updates, frozenset(_GAME_METADATA_FIELDS), actor=actor)
    playnite.restore_locks(game, kept_locks)

    # Keep sort_title in sync if title changed but sort_title wasn't explicitly
    # set, or was cleared (a blank sorting name means "sort by the title")
    if ("title" in updates and "sort_title" not in updates) or (
        "sort_title" in updates and not updates["sort_title"]
    ):
        game.sort_title = _derive_sort_title(game.title)

    # first time this game reaches Mastered, record when — a later status
    # change away from and back to Mastered doesn't overwrite it, so it
    # stays "when I first 100%'d this" rather than "when I last did"
    if updates.get("status") == GameStatus.MASTERED and game.completion_date is None:
        game.completion_date = int(time.time())

    await manual.record_user_status(db, game, manual_status)

    await _record_update_observation(db, game, actor, sent)

    target_folder = game.folder_location
    try:
        if target_folder != old_folder:
            await game_folders.commit_folder_move(db, game, old_folder, _DATA_ROOT)
        else:
            await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise _duplicate_folder_error(target_folder) from exc
    except (game_folders.FolderStorageError, OSError) as exc:
        raise HTTPException(
            status_code=409,
            detail=f"Could not save the game folder. {exc} Retry saving its folder name to recover.",
        ) from exc

    return game


async def _record_update_observation(
    db: AsyncSession, game: Game, actor: AuthenticatedActor, sent: dict
) -> None:
    """Record what an update reported as evidence; only the fields it carried.

    A Playnite API key's update is Playnite's observation of its entry. A user
    editing their own manual entry updates the manual observation. Edits to any
    other game are the user's tracking choices, not source evidence."""
    is_api_key = isinstance(actor, AuthenticatedActor) and actor.credential_kind == "api_key"
    if is_api_key and game.playnite_guid is not None:
        await record_playnite_fields(db, game.user_id, game.id, game.playnite_guid, sent)
    elif await has_manual_observation(db, game.user_id, game.id):
        await record_manual_fields(db, game.user_id, game.id, sent)


router.include_router(game_metadata.router)


@router.get("/{game_id}/field-changes", response_model=list[GameFieldChangeRead])
async def list_field_changes(
    game_id: UUID,
    db: AsyncSession = _DB_DEPENDENCY,
    current_user: User = _CURRENT_USER_DEPENDENCY,
) -> list[GameFieldChange]:
    """Most-recent-first metadata history for one game."""
    await _get_game_or_404(game_id, db, current_user.id)
    stmt = (
        select(GameFieldChange)
        .where(GameFieldChange.game_id == game_id)
        .order_by(GameFieldChange.changed_at.desc())
        .limit(100)
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


@router.patch("/bulk-update")
async def bulk_update_games(
    payload: GameBulkUpdate,
    db: AsyncSession = _DB_DEPENDENCY,
    current_user: User = _CURRENT_USER_DEPENDENCY,
) -> dict[str, int]:
    """Apply the same field values to many of the caller's games at once —
    e.g. fixing status across a batch, or filling in developer/publisher
    for titles a metadata search couldn't confidently match on its own."""
    updates = _drop_nulls_for_required_fields(
        payload.model_dump(exclude_unset=True, exclude={"game_ids"})
    )
    if not updates:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No fields to update.")

    result = await db.execute(
        select(Game).where(
            Game.user_id == current_user.id,
            Game.id.in_(payload.game_ids),
            Game.deleted_at.is_(None),
        )
    )
    games = result.scalars().all()
    for game in games:
        previous = game.status
        for field, value in updates.items():
            setattr(game, field, value)
        if updates.get("status") == GameStatus.MASTERED and game.completion_date is None:
            game.completion_date = int(time.time())
        if updates.get("status") is not None and updates["status"] != previous:
            await manual.set_completion(db, current_user.id, game.id, updates["status"], game=game)
    await db.commit()
    return {"updated": len(games)}


@router.delete("/delete/{game_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_game(
    game_id: UUID,
    db: AsyncSession = _DB_DEPENDENCY,
    current_user: User = _CURRENT_USER_DEPENDENCY,
) -> None:
    """Soft-delete: moves the game's entire folder to trash and marks the
    row deleted rather than removing anything — restorable for 7 days.
    This is the highest-blast-radius delete in the app (screenshots,
    clips, notes, achievements, saves, everything lives under this one
    folder), so it gets the same protection as game archives instead of
    the instant, permanent delete it had before."""
    game = await _get_game_or_404(game_id, db, current_user.id, for_update=True, for_storage=True)
    if game.folder_location:
        move_game_to_trash(
            _DATA_ROOT / str(game.user_id) / "games" / game.folder_location, _DATA_ROOT, game_id
        )
    game.deleted_at = int(time.time())
    await db.commit()


@router.get("/trash")
async def list_game_trash(
    db: AsyncSession = _DB_DEPENDENCY,
    current_user: User = _CURRENT_USER_DEPENDENCY,
) -> list[dict]:
    result = await db.execute(
        select(Game)
        .where(Game.user_id == current_user.id, Game.deleted_at.is_not(None))
        .order_by(Game.deleted_at.desc())
    )
    trashed = []
    for game in result.scalars().all():
        assert game.deleted_at is not None  # guaranteed by the deleted_at.is_not(None) filter above
        trashed.append(
            {
                "id": str(game.id),
                "title": game.title,
                "deleted_at": game.deleted_at,
                "purge_at": game.deleted_at + RETENTION_SECONDS,
            }
        )
    return trashed


@router.post("/{game_id}/restore", response_model=GameRead)
async def restore_game(
    game_id: UUID,
    db: AsyncSession = _DB_DEPENDENCY,
    current_user: User = _CURRENT_USER_DEPENDENCY,
) -> Game:
    game = await _get_game_or_404(
        game_id, db, current_user.id, include_deleted=True, for_update=True, for_storage=True
    )
    if game.deleted_at is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Game isn't deleted.")
    if game.folder_location:
        # a new active game may have claimed this folder name while this one
        # sat in trash (the partial unique index only protects active rows) —
        # check before touching any files, not after, so a rejected restore
        # never leaves the folder half-moved
        await _ensure_folder_location_available(
            game.folder_location, current_user.id, db, exclude_game_id=game_id
        )
        restore_game_from_trash(
            _DATA_ROOT / str(game.user_id) / "games" / game.folder_location, _DATA_ROOT, game_id
        )
    game.deleted_at = None
    await db.commit()
    await db.refresh(game)
    return game


# Compatibility exports for existing callers and stable OpenAPI model identities.
__all__ = [
    "ALLOWED_ASSET_KINDS",
    "AssetUrlRequest",
    "ChecklistItemUpdate",
    "ChecklistItemWrite",
    "ChecklistReorder",
    "DetectDatesRequest",
    "FIELD_CHANGE_TRACKED_FIELDS",
    "GameFileUpdate",
    "MediaItemUpdate",
    "MetadataRefreshRequest",
    "MetadataSearchResponse",
    "NoteRename",
    "NoteWrite",
    "ProfileUpdate",
    "ProfileWrite",
    "WiseOldManSyncRequest",
    "_DATA_ROOT",
    "_GAME_METADATA_FIELDS",
    "_NON_NULLABLE_UPDATE_FIELDS",
    "_NOTE_NAME_PATTERN",
    "_checklist_item_to_dict",
    "_derive_sort_title",
    "_drop_nulls_for_required_fields",
    "_duplicate_folder_error",
    "_ensure_folder_location_available",
    "_field_change_value_to_text",
    "_game_file_subdir",
    "_game_file_to_dict",
    "_game_note_path",
    "_get_game_or_404",
    "_get_profile_or_404",
    "_media_item_to_dict",
    "_metadata_value_is_present",
    "_normalize_metadata_title",
    "_normalize_note_name",
    "_note_summary",
    "_profile_to_dict",
    "_record_field_changes",
    "_record_stat_snapshot",
    "_scan_settings_to_preferences",
    "_sync_game_file_items",
    "_thumb_dir",
    "create_checklist_item",
    "create_game_note",
    "create_game_profile",
    "delete_checklist_item",
    "delete_game_file",
    "delete_game_note",
    "delete_game_profile",
    "delete_game_screenshot",
    "detect_media_dates",
    "download_game_asset",
    "get_clip_thumbnail",
    "get_game_asset",
    "get_game_file",
    "get_game_note",
    "get_game_screenshot",
    "get_profile_stat_history",
    "list_game_checklist",
    "list_game_file_trash",
    "list_game_files",
    "list_game_note_summaries",
    "list_game_notes",
    "list_game_profile_trash",
    "list_game_profiles",
    "list_game_screenshot_trash",
    "list_game_screenshots",
    "refresh_game_metadata",
    "rename_game_note",
    "reorder_checklist",
    "restore_game_file",
    "restore_game_profile",
    "restore_game_screenshot",
    "save_clip_thumbnail",
    "search_metadata",
    "sync_profile_wiseoldman",
    "update_checklist_item",
    "update_game_file",
    "update_game_note",
    "update_game_profile",
    "update_media_item",
    "upload_game_asset",
    "upload_game_files",
    "upload_game_screenshots",
]
