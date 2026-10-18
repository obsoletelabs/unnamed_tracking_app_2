"""Game files routes, composed by the games router."""

import time
from pathlib import Path
from typing import Annotated, Any, Literal, cast
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.routes.utils.games import (
    _DATA_ROOT,
    _get_game_or_404,
)
from src.core.app_integrations import get_upload_limits_mb
from src.core.auth import get_current_user
from src.database.models.game import Game
from src.database.models.game_file_item import GameFileItem
from src.database.models.user import User
from src.database.session import get_db
from src.features.game_identity.copies import page_copies
from src.features.trash.media_trash import move_media_file_to_trash, restore_media_file_from_trash
from src.features.trash.sweep import RETENTION_SECONDS
from src.helpers.media import (
    UploadTooLargeError,
    display_filename,
    list_media,
    original_filename,
    save_upload_stream,
)
from src.helpers.media_dates import detect_date
from src.helpers.range_response import SAFE_FILE_HEADERS

_FILES_DEFAULT = File(None, alias="files")


_FILE_DEFAULT = File(None, alias="file")


_DB_DEPENDENCY = Depends(get_db)


_CURRENT_USER_DEPENDENCY = Depends(get_current_user)


_FILE_MODIFIED_FORM = Form(None)


router = APIRouter()

GameFileKind = Literal["doc", "modpack"]
# "save" and "world_save" moved to game_archives.py — named, versioned
# archives instead of an anonymous single flat file each

_GAME_FILE_SUBDIRS: dict[GameFileKind, str] = {
    "doc": "docs",
    "modpack": "modpacks",
}
# The Administration -> Limits cap for each kind; neither follows the image cap.
_GAME_FILE_LIMITS: dict[GameFileKind, str] = {
    "doc": "max_doc_size_mb",
    "modpack": "max_world_save_size_mb",
}


def _game_file_subdir(kind: GameFileKind) -> str:
    return _GAME_FILE_SUBDIRS[kind]


async def _sync_game_file_items(game_id: UUID, game_dir: Path, db: AsyncSession) -> None:
    """Docs/modpacks used to be tracked only on disk, with no DB row at
    all — this backfills a row (created_at from the file's mtime) for
    anything already sitting in the active folder that doesn't have one
    yet, so existing files from before this migration still show up
    without a one-time manual migration script (same approach as
    media.py's _sync_inbox_items)."""
    existing = await db.execute(
        select(GameFileItem.kind, GameFileItem.filename).where(
            GameFileItem.game_id == game_id, GameFileItem.deleted_at.is_(None)
        )
    )
    known = set(existing.all())
    added = False
    for kind in ("doc", "modpack"):
        for filename in list_media(game_dir / _game_file_subdir(kind)):  # type: ignore[arg-type]
            if (kind, filename) in known:
                continue
            path = game_dir / _game_file_subdir(kind) / filename  # type: ignore[arg-type]
            try:
                mtime = int(path.stat().st_mtime)
            except OSError:
                mtime = int(time.time())
            db.add(GameFileItem(game_id=game_id, kind=kind, filename=filename, created_at=mtime))
            added = True
    if added:
        await db.flush()


def _game_file_to_dict(item: GameFileItem, game_id: UUID, game_dir: Path) -> dict:
    path = game_dir / _game_file_subdir(cast(GameFileKind, item.kind)) / item.filename
    return {
        "id": str(item.id),
        "game_id": str(game_id),
        "filename": item.filename,
        "original_filename": item.original_filename,
        "kind": item.kind,
        "size": path.stat().st_size if path.is_file() else 0,
        "url": f"/api/game/{game_id}/files/{item.kind}/{item.filename}",
        "created_at": item.created_at,
        "title": item.title,
        "note": item.note,
        "tags": item.tags,
        "taken_at": item.taken_at,
        "taken_source": item.taken_source,
    }


# Keep the established workflow and public parameters together.
# pylint: disable=duplicate-code,too-many-locals,too-many-positional-arguments
@router.post("/{game_id}/files/{kind}")
async def upload_game_files(
    game_id: UUID,
    kind: GameFileKind,
    files: list[UploadFile] | None = _FILES_DEFAULT,
    file: UploadFile | None = _FILE_DEFAULT,
    last_modified: list[int] | None = _FILE_MODIFIED_FORM,
    db: AsyncSession = _DB_DEPENDENCY,
    current_user: User = _CURRENT_USER_DEPENDENCY,
) -> dict[str, list[dict]]:
    """Generic file attachments for a game. Docs/manuals and modpacks can be
    any format, so unlike screenshots/clips there's no content-type
    validation, only a size cap of their own: a manual or an illustrated Word
    file is often larger than a screenshot, and a modpack zip is routinely
    hundreds of MB to a few GB. They only ever download as inert attachments
    (see get_game_file)."""
    game = await _get_game_or_404(game_id, db, current_user.id, for_storage=True)
    if not game.folder_location:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Game folder_location is missing."
        )

    limit_mb = (await get_upload_limits_mb(db))[_GAME_FILE_LIMITS[kind]]
    max_bytes = limit_mb * 1024 * 1024
    # Initializing deployment limits can commit; take a fresh storage lock afterward.
    game = await _get_game_or_404(game_id, db, current_user.id, for_storage=True)
    # Accept both the current plural field used by the frontend and the
    # legacy/single-file field used by older clients.
    uploads = list(files or [])
    if file is not None:
        uploads.append(file)
    if not uploads:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one file is required.",
        )
    dest_dir = (
        _DATA_ROOT / str(game.user_id) / "games" / game.folder_location / _game_file_subdir(kind)
    )
    results: list[dict] = []
    for index, upload in enumerate(uploads):
        # Streamed to disk: these caps are far larger than is sensible to hold in memory.
        try:
            saved_path, size = await save_upload_stream(
                upload, dest_dir, upload.filename or "file", max_bytes
            )
        except UploadTooLargeError:
            results.append(
                {
                    "filename": upload.filename,
                    "status": "rejected",
                    "reason": f"Larger than {limit_mb} MB.",
                }
            )
            continue
        # A doc has no photo or video date of its own: only its name or modified time.
        taken_at, taken_source = detect_date(
            b"",
            upload.filename or "",
            "doc",
            last_modified[index] if last_modified and index < len(last_modified) else None,
        )
        db.add(
            GameFileItem(
                game_id=game_id,
                kind=kind,
                filename=saved_path.name,
                original_filename=original_filename(upload.filename or "file"),
                taken_at=taken_at,
                taken_source=taken_source,
            )
        )
        results.append({"filename": saved_path.name, "status": "saved", "size": size})

    await db.commit()
    return {"results": results}


# pylint: enable=duplicate-code,too-many-locals,too-many-positional-arguments


async def _owner_files(
    db: AsyncSession, owner: Game, kind: GameFileKind, label: dict | None
) -> list[dict]:
    """One copy's live files of a kind, labelled with the copy on a grouped page."""
    if not owner.folder_location:
        return []
    game_dir = _DATA_ROOT / str(owner.user_id) / "games" / owner.folder_location
    await _sync_game_file_items(owner.id, game_dir, db)
    result = await db.execute(
        select(GameFileItem)
        .where(
            GameFileItem.game_id == owner.id,
            GameFileItem.kind == kind,
            GameFileItem.deleted_at.is_(None),
        )
        .order_by(GameFileItem.filename)
    )
    files = []
    for item in result.scalars().all():
        entry = _game_file_to_dict(item, owner.id, game_dir)
        if label is not None:
            entry["copy"] = label
        files.append(entry)
    return files


@router.get("/{game_id}/files/{kind}")
async def list_game_files(
    game_id: UUID,
    kind: GameFileKind,
    db: AsyncSession = _DB_DEPENDENCY,
    current_user: User = _CURRENT_USER_DEPENDENCY,
    group: Annotated[
        bool, Query(description="Also list files of every copy on this game's page.")
    ] = False,
) -> dict[str, list[dict]]:
    game = await _get_game_or_404(game_id, db, current_user.id, for_storage=True)
    copies = await page_copies(db, current_user.id, game_id) if group else []
    owners = {game.id: game}
    if group:
        # Each copy keeps its files in its own folder (#274).
        for row in await db.scalars(
            select(Game).where(
                Game.id.in_([c.game_id for c in copies if c.game_id != game_id]),
                Game.user_id == current_user.id,
            )
        ):
            owners[row.id] = row
    labels = {c.game_id: c.as_dict() for c in copies}
    files = []
    for owner in owners.values():
        files.extend(await _owner_files(db, owner, kind, labels.get(owner.id)))
    if len(owners) > 1:
        files.sort(key=lambda entry: (not entry["copy"]["is_main"], entry.get("filename", "")))
    response = {"files": files}
    await db.commit()
    return response


class GameFileUpdate(BaseModel):
    __module__ = "src.api.routes.games"
    title: str | None = Field(default=None, max_length=200)
    note: str | None = None
    tags: list[str] | None = None
    taken_at: int | None = None
    taken_source: Literal["manual", "achievement"] | None = None


# Keep the established workflow and public parameters together.
# pylint: disable=duplicate-code,too-many-positional-arguments
@router.patch("/{game_id}/files/{kind}/by-id/{item_id}")
async def update_game_file(
    game_id: UUID,
    kind: GameFileKind,
    item_id: UUID,
    payload: GameFileUpdate,
    db: AsyncSession = _DB_DEPENDENCY,
    current_user: User = _CURRENT_USER_DEPENDENCY,
) -> dict:
    game = await _get_game_or_404(game_id, db, current_user.id, for_storage=True)
    item = await db.scalar(
        select(GameFileItem).where(
            GameFileItem.id == item_id,
            GameFileItem.game_id == game_id,
            GameFileItem.kind == kind,
            GameFileItem.deleted_at.is_(None),
        )
    )
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found.")
    changes = payload.model_dump(exclude_unset=True)
    if "title" in changes and changes["title"] is not None:
        changes["title"] = changes["title"].strip() or None
    source = changes.pop("taken_source", None)
    for field, value in changes.items():
        setattr(item, field, value)
    if "taken_at" in changes:
        item.taken_source = (source or "manual") if changes["taken_at"] is not None else None
    game_dir = _DATA_ROOT / str(game.user_id) / "games" / (game.folder_location or "")
    response = _game_file_to_dict(item, game_id, game_dir)
    await db.commit()
    return response


# pylint: enable=duplicate-code,too-many-positional-arguments


@router.get("/{game_id}/files/{kind}/trash")
async def list_game_file_trash(
    game_id: UUID,
    kind: GameFileKind,
    db: AsyncSession = _DB_DEPENDENCY,
    current_user: User = _CURRENT_USER_DEPENDENCY,
    group: Annotated[
        bool,
        Query(
            description="Also list trashed items of every copy on this game's page, with their copy."
        ),
    ] = False,
) -> dict[str, list[dict]]:
    await _get_game_or_404(game_id, db, current_user.id, for_storage=True)
    copies = (
        {c.game_id: c for c in await page_copies(db, current_user.id, game_id)} if group else {}
    )
    result = await db.execute(
        select(GameFileItem)
        .where(
            GameFileItem.game_id.in_(copies or {game_id}),
            GameFileItem.kind == kind,
            GameFileItem.deleted_at.is_not(None),
        )
        .order_by(GameFileItem.deleted_at.desc())
    )
    files = []
    for item in result.scalars().all():
        assert item.deleted_at is not None  # guaranteed by the deleted_at.is_not(None) filter above
        entry: dict[str, Any] = {
            "id": str(item.id),
            "game_id": str(item.game_id),
            "filename": item.filename,
            "original_filename": item.original_filename,
            "deleted_at": item.deleted_at,
            "purge_at": item.deleted_at + RETENTION_SECONDS,
        }
        if group:
            entry["copy"] = copies[item.game_id].as_dict()
        files.append(entry)
    return {"files": files}


# Parallel routes/models intentionally share this shape.
# pylint: disable=duplicate-code
@router.get("/{game_id}/files/{kind}/{filename}", response_class=FileResponse)
async def get_game_file(
    game_id: UUID,
    kind: GameFileKind,
    filename: str,
    db: AsyncSession = _DB_DEPENDENCY,
    current_user: User = _CURRENT_USER_DEPENDENCY,
) -> FileResponse:
    game = await _get_game_or_404(game_id, db, current_user.id, for_storage=True)
    path = (
        _DATA_ROOT
        / str(game.user_id)
        / "games"
        / (game.folder_location or "")
        / _game_file_subdir(kind)
        / Path(filename).name
    )
    if not path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found.")
    # Documents/modpacks download as attachments. Preserve the recorded original
    # name, falling back to the legacy storage name for older files.
    original_name = await db.scalar(
        select(GameFileItem.original_filename).where(
            GameFileItem.game_id == game_id,
            GameFileItem.kind == kind,
            GameFileItem.filename == path.name,
            GameFileItem.deleted_at.is_(None),
        )
    )
    return FileResponse(
        path,
        filename=display_filename(path.name, original_name),
        media_type="application/octet-stream",
        headers=SAFE_FILE_HEADERS,
    )


# pylint: enable=duplicate-code


@router.delete("/{game_id}/files/{kind}/{filename}")
async def delete_game_file(
    game_id: UUID,
    kind: GameFileKind,
    filename: str,
    db: AsyncSession = _DB_DEPENDENCY,
    current_user: User = _CURRENT_USER_DEPENDENCY,
) -> dict[str, str]:
    """Soft-delete: moves the file to trash and marks its row deleted
    rather than removing it — restorable for 7 days, same as every other
    delete path in the app (features/trash/sweep.py does the eventual
    real deletion)."""
    game = await _get_game_or_404(game_id, db, current_user.id, for_storage=True)
    game_dir = _DATA_ROOT / str(game.user_id) / "games" / (game.folder_location or "")
    await _sync_game_file_items(game_id, game_dir, db)
    name = Path(filename).name
    item = await db.scalar(
        select(GameFileItem).where(
            GameFileItem.game_id == game_id,
            GameFileItem.kind == kind,
            GameFileItem.filename == name,
            GameFileItem.deleted_at.is_(None),
        )
    )
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found.")
    path = game_dir / _game_file_subdir(kind) / name
    move_media_file_to_trash(path, game_dir, kind)
    item.deleted_at = int(time.time())
    await db.commit()
    return {"status": "trashed", "filename": name}


@router.post("/{game_id}/files/{kind}/{filename}/restore")
async def restore_game_file(
    game_id: UUID,
    kind: GameFileKind,
    filename: str,
    db: AsyncSession = _DB_DEPENDENCY,
    current_user: User = _CURRENT_USER_DEPENDENCY,
) -> dict[str, str]:
    game = await _get_game_or_404(game_id, db, current_user.id, for_storage=True)
    name = Path(filename).name
    # The trash sweep locks the row it purges; restore waits rather than racing it.
    item = await db.scalar(
        select(GameFileItem)
        .where(
            GameFileItem.game_id == game_id,
            GameFileItem.kind == kind,
            GameFileItem.filename == name,
            GameFileItem.deleted_at.is_not(None),
        )
        .with_for_update()
    )
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Deleted file not found.")
    game_dir = _DATA_ROOT / str(game.user_id) / "games" / (game.folder_location or "")
    restore_media_file_from_trash(name, game_dir / _game_file_subdir(kind), game_dir, kind)
    item.deleted_at = None
    await db.commit()
    return {"status": "restored", "filename": name}
