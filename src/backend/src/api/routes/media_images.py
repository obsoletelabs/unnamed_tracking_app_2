"""Local copies of the posters and backdrops of movies, TV shows and anime."""

import asyncio
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.image_responses import original_image_response
from src.core.auth import get_current_user
from src.database.models.anime import Anime, AnimeEpisode, AnimeSeason
from src.database.models.movies import Movie
from src.database.models.tv_show import TVEpisode, TVSeason, TVShow
from src.database.models.user import User
from src.database.session import get_db
from src.helpers.image_prefetch import MEDIA_ROOT, WIDTHS
from src.helpers.remote_images import RemoteImageError, cache_path
from src.helpers.remote_images import fetch_if_missing as fetch_and_store

router = APIRouter(
    prefix="/api/media-image",
    tags=["media"],
    dependencies=[Depends(get_current_user)],
)

_DATA_ROOT = MEDIA_ROOT
_MODELS: dict[str, Any] = {"movie": Movie, "tv": TVShow, "anime": Anime}
_EPISODES: dict[str, tuple[Any, Any, Any]] = {
    "tv": (TVEpisode, TVSeason, TVShow),
    "anime": (AnimeEpisode, AnimeSeason, Anime),
}


@router.get("/{media_type}/{item_id}/{which}")
async def get_media_image(
    media_type: Literal["movie", "tv", "anime"],
    item_id: UUID,
    which: Literal["poster", "backdrop", "hero"],
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Response:
    """The title's poster or backdrop from this server's own disk, downloading
    and shrinking it the first time. If the download fails the browser is sent
    to the original address instead, so a picture never just goes missing."""
    model = _MODELS[media_type]
    row = await db.scalar(
        select(model).where(model.id == item_id, model.user_id == current_user.id)
    )
    url: str | None = None
    if row:
        # "hero" is the picture behind a page's title: the backdrop, or the poster
        # when the title has no backdrop
        url = (
            (row.backdrop_url or row.poster_url)
            if which == "hero"
            else getattr(row, f"{which}_url", None)
        )
    if not url:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such image.")

    # the hero of a title with a backdrop is that same picture: one file, not two
    name = "backdrop" if which == "hero" and row and row.backdrop_url else which
    return await _cached_image(current_user, media_type, item_id, name, url, width=WIDTHS[which])


@router.get("/{media_type}/episode/{episode_id}/still")
async def get_episode_still(
    media_type: Literal["tv", "anime"],
    episode_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Response:
    """An episode's still from this server's own disk, like a poster: the address comes
    from provider or plugin data, so the browser never loads it from that host directly."""
    episode, season, show = _EPISODES[media_type]
    url = await db.scalar(
        select(episode.still_url)
        .join(season, season.id == episode.season_id)
        .join(show, show.id == season.show_id)
        .where(episode.id == episode_id, show.user_id == current_user.id)
    )
    if not url:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such image.")
    return await _cached_image(
        current_user, f"{media_type}-episode", episode_id, "still", url, width=WIDTHS["still"]
    )


async def _cached_image(
    user: User, media_type: str, item_id: UUID, name: str, url: str, *, width: int
) -> Response:
    target = cache_path(_DATA_ROOT / str(user.id) / ".cache", media_type, str(item_id), name, url)
    if not target.is_file():
        try:
            await asyncio.to_thread(fetch_and_store, url, target, width)
        except RemoteImageError:
            return original_image_response(url)
    return FileResponse(
        target,
        media_type="image/jpeg",
        headers={"Cache-Control": "private, max-age=3600, must-revalidate"},
    )
