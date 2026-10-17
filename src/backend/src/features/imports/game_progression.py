"""Conservative status progression from fresh, authoritative provider evidence."""

import time

from src.database.models.game import Game, GameStatus


def apply_provider_progress(
    game: Game,
    *,
    owned: bool = False,
    playtime_seconds: int | None = None,
    total_achievements: int = 0,
    unlocked_achievements: int = 0,
) -> None:
    """Call under the import row lock, using provider data rather than local overrides.

    Wishlist needs an owned-library result, not an achievement refresh. Every
    existing Played choice is retained: old rows cannot distinguish manual and
    automatic Played. Empty/unavailable evidence never implies completion.
    """
    if "status" in (game.locked_fields or []):
        return  # the user chose this status by hand (#97)
    if game.deleted_at is not None or game.status not in {
        GameStatus.BACKLOG,
        GameStatus.WISHLIST,
    }:
        return
    if game.status == GameStatus.WISHLIST and not owned:
        return
    if unlocked_achievements >= total_achievements > 0:
        game.status = GameStatus.MASTERED
        if game.completion_date is None:
            game.completion_date = int(time.time())
    elif (playtime_seconds or 0) > 0 or unlocked_achievements > 0:
        game.status = GameStatus.PLAYED
    elif owned:
        game.status = GameStatus.BACKLOG
