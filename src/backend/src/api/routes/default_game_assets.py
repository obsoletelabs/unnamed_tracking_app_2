"""Fallback artwork for games that do not have stored cover art."""

import asyncio
from html import escape
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.routes.utils.games import _DATA_ROOT, ALLOWED_ASSET_KINDS, _get_game_or_404
from src.core.auth import get_current_user
from src.database.models.user import User
from src.database.session import get_db
from src.helpers.artwork_revisions import (
    artwork_response,
    bytes_revision,
    stored_file_revision,
)
from src.helpers.asset_previews import ensure_preview, preview_path
from src.helpers.async_storage import finish_storage_operation
from src.helpers.game_cover_placeholder import cover_palette
from src.helpers.save_game_asset import ASSET_FILENAMES, AssetKind

router = APIRouter(
    prefix="/api/game",
    tags=["game"],
    dependencies=[Depends(get_current_user)],
)


def _default_cover_svg(game_id: UUID, title: str) -> str:
    """Build a lightweight, title-aware SVG fallback cover."""
    background, foreground, accent = cover_palette(game_id)
    safe_title = escape(title.strip() or "Unknown Game")
    return f"""<svg xmlns=\"http://www.w3.org/2000/svg\" viewBox=\"0 0 600 900\" role=\"img\"
    aria-label=\"{safe_title} default cover\">
  <title>{safe_title} — NO COVER ART</title>
  <defs>
    <linearGradient id=\"bg\" x1=\"0\" y1=\"0\" x2=\"1\" y2=\"1\">
      <stop offset=\"0\" stop-color=\"{foreground}\"/>
      <stop offset=\".78\" stop-color=\"{background}\"/>
    </linearGradient>
    <radialGradient id=\"glow\" cx=\"50%\" cy=\"38%\" r=\"74%\">
      <stop offset=\"0\" stop-color=\"{accent}\" stop-opacity=\".27\"/>
      <stop offset=\"1\" stop-color=\"{accent}\" stop-opacity=\"0\"/>
    </radialGradient>
  </defs>
  <rect width=\"600\" height=\"900\" fill=\"url(#bg)\"/>
  <rect width=\"600\" height=\"900\" fill=\"url(#glow)\"/>
  <svg x=\"138\" y=\"207\" width=\"324\" height=\"486\" viewBox=\"0 0 64 64\"
    fill=\"none\" stroke=\"#aeb7c2\" stroke-width=\"3\" stroke-linejoin=\"round\">
    <path d=\"M17 24h30c6 0 9 4 10 11l2 13c1 6-6 9-10 4l-5-6H20l-5 6c-4 5-11 2-10-4l2-13c1-7 4-11 10-11Z\"/>
    <path d=\"M19 31v12m-6-6h12m17-3h.1M49 39h.1\" stroke-linecap=\"round\"/>
  </svg>
</svg>"""


# Parallel routes/models intentionally share this shape.
# pylint: disable=duplicate-code
@router.get("/preview-cover")
async def preview_default_cover(
    title: Annotated[str, Query(max_length=80)] = "Preview Game",
) -> Response:
    """Preview the existing fallback artwork without creating a library entry."""
    return Response(
        content=_default_cover_svg(UUID("00000000-0000-0000-0000-000000000001"), title),
        media_type="image/svg+xml",
        headers={"Cache-Control": "private, max-age=3600, must-revalidate"},
    )


# pylint: enable=duplicate-code


# Parallel routes/models intentionally share this shape.
# pylint: disable=duplicate-code,too-many-positional-arguments
@router.get("/{game_id}/assets/{asset_kind}")
async def get_game_asset_with_fallback(
    request: Request,
    game_id: UUID,
    asset_kind: AssetKind,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
    # Preview stored artwork at the requested display width.
    w: Annotated[int | None, Query(ge=64, le=4096)] = None,
    # Revision of the stored artwork, from an upload response; see artwork_revisions.
    v: Annotated[str | None, Query(max_length=64)] = None,
) -> Response:
    """Serve stored artwork (or a resized JPEG of it with `w`), falling back to
    generated cover art when needed. Every answer carries an ETag derived from the
    served bytes and honours If-None-Match with a 304."""
    if asset_kind not in ALLOWED_ASSET_KINDS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported asset kind '{asset_kind}'. Supported values: {sorted(ALLOWED_ASSET_KINDS)}",
        )

    game = await _get_game_or_404(game_id, db, current_user.id, for_storage=True)
    asset_path = (
        _DATA_ROOT
        / str(game.user_id)
        / "games"
        / game.folder_location
        / ASSET_FILENAMES[asset_kind]
    )

    if asset_path.is_file():
        media_type = "image/png"
        source_revision = await stored_file_revision(asset_path)
        served_path = asset_path
        if w is not None:
            served_path, media_type = await finish_storage_operation(
                asyncio.to_thread(
                    ensure_preview,
                    asset_path,
                    preview_path(
                        _DATA_ROOT / str(game.user_id) / ".cache", str(game_id), asset_kind, w
                    ),
                    w,
                )
            )
        revision = (
            source_revision
            if served_path == asset_path
            else await stored_file_revision(served_path)
        )
        return artwork_response(
            request,
            revision=revision,
            source_revision=source_revision,
            version=v,
            media_type=media_type,
            path=served_path,
        )

    if asset_kind != "key_art":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Asset '{asset_kind}' has not been uploaded for game {game_id}.",
        )

    cover = _default_cover_svg(game.id, game.title).encode("utf-8")
    revision = bytes_revision(cover)
    return artwork_response(
        request,
        revision=revision,
        source_revision=revision,
        version=None,
        media_type="image/svg+xml",
        content=cover,
        # Artwork can arrive at this same URL after an upload or metadata refresh,
        # so the generated cover is always revalidated (a 304 is still cheap).
        cache_control="private, no-cache",
    )


# pylint: enable=duplicate-code,too-many-positional-arguments
