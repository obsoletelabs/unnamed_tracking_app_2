"""Permanent removal of trashed movies, TV shows and anime.

A trashed title is only a database row (a show or anime also has its seasons
and episodes), unlike the game files that sweep.py moves aside on disk. Both
ways of removing titles for good go through `purge_trashed_titles`, so they
share the same ownership, deletion-time cutoff, locking and per-title
transactions:

- Empty trash: the owner confirms permanent deletion of one kind's trash.
- Retention: an owner who chose 7, 14, 30 or 90 days in their preferences has
  older trashed titles removed by the existing trash sweep. Never (the default)
  removes nothing.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.functions import count as sql_count

from src.core.preferences import MEDIA_TRASH_RETENTION_DAYS
from src.database.models.anime import Anime
from src.database.models.movies import Movie
from src.database.models.tv_show import TVShow
from src.database.models.user_preferences import UserPreferences
from src.database.session import SessionLocal

logger = logging.getLogger(__name__)

RETENTION_KEY = "media_trash_retention_days"
TITLE_MODELS: tuple[Any, ...] = (Movie, TVShow, Anime)
# One Empty trash request removes at most this many titles; the caller repeats
# it for the rest, so a very large trash never becomes one long transaction.
EMPTY_BATCH_SIZE = 200
# Each sweep removes at most this many expired titles per kind and account, and
# at most RETENTION_SWEEP_LIMIT in total. Anything left waits for the next sweep.
RETENTION_BATCH_SIZE = 200
RETENTION_SWEEP_LIMIT = 2000
_DAY_SECONDS = 24 * 60 * 60


@dataclass
class TitlePurge:
    """What one purge pass did. A failed title is still in the trash, unchanged."""

    deleted: int = 0
    failed: list[dict[str, Any]] = field(default_factory=list)


def _trashed(model: Any, user_id: UUID, deleted_through: int) -> list[Any]:
    return [
        model.user_id == user_id,
        model.deleted_at.is_not(None),
        model.deleted_at <= deleted_through,
    ]


async def purge_trashed_titles(
    db: AsyncSession, model: Any, user_id: UUID, *, deleted_through: int, limit: int
) -> TitlePurge:
    """Delete up to `limit` of the account's titles trashed at or before
    `deleted_through`, oldest first. The caller commits.

    Only the owner's rows still in the trash qualify. They are locked first, so
    a concurrent restore either finishes before (and the title is skipped) or
    waits until this transaction ends. Each title is removed in its own
    savepoint with its seasons and episodes: a failure rolls back that title
    alone, which stays in the trash and is reported, while the others proceed.
    """
    rows = (
        await db.scalars(
            select(model)
            .where(*_trashed(model, user_id, deleted_through))
            .order_by(model.deleted_at, model.id)
            .limit(limit)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).all()
    outcome = TitlePurge()
    for row in rows:
        title = {"id": str(row.id), "title": row.title, "deleted_at": row.deleted_at}
        try:
            async with db.begin_nested():
                await db.delete(row)
        except SQLAlchemyError:
            # the rolled-back row may be expired; report it from what was read
            logger.warning(
                "Could not permanently delete trashed %s %s",
                model.__name__,
                title["id"],
                exc_info=True,
            )
            outcome.failed.append(title)
        else:
            outcome.deleted += 1
    return outcome


async def count_trashed_titles(
    db: AsyncSession, model: Any, user_id: UUID, *, deleted_through: int
) -> int:
    return int(
        await db.scalar(
            select(sql_count()).select_from(model).where(*_trashed(model, user_id, deleted_through))
        )
        or 0
    )


async def empty_trash(
    db: AsyncSession, model: Any, user_id: UUID, *, deleted_through: int | None
) -> dict[str, Any]:
    """Permanently delete one batch of the owner's trashed titles of one kind.

    `deleted_through` is the newest deletion time the owner saw when confirming,
    so a title trashed afterwards is never included. `remaining` counts what is
    still in the trash within that range, failed titles included."""
    through = int(time.time()) if deleted_through is None else deleted_through
    outcome = await purge_trashed_titles(
        db, model, user_id, deleted_through=through, limit=EMPTY_BATCH_SIZE
    )
    await db.commit()
    return {
        "deleted": outcome.deleted,
        "failed": len(outcome.failed),
        "failed_items": outcome.failed,
        "remaining": await count_trashed_titles(db, model, user_id, deleted_through=through),
        "deleted_through": through,
    }


def retention_days(value: Any) -> int:
    """A stored choice that is not exactly one of the offered periods means Never."""
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return value if value in MEDIA_TRASH_RETENTION_DAYS else 0


async def _locked_retention_days(db: AsyncSession, user_id: UUID) -> int:
    # FOR SHARE: a concurrent preference change waits for this purge, or
    # commits first and is honored here (save_preferences locks FOR UPDATE).
    value = await db.scalar(
        select(UserPreferences.data[RETENTION_KEY])
        .where(UserPreferences.user_id == user_id)
        .with_for_update(read=True)
    )
    return retention_days(value)


async def _purge_account(user_id: UUID, now: int, budget: int) -> TitlePurge:
    """One account's expired titles, in a single transaction."""
    total = TitlePurge()
    async with SessionLocal() as db:
        days = await _locked_retention_days(db, user_id)
        if not days:
            return total
        cutoff = now - days * _DAY_SECONDS
        for model in TITLE_MODELS:
            limit = min(RETENTION_BATCH_SIZE, budget - total.deleted - len(total.failed))
            if limit <= 0:
                break
            outcome = await purge_trashed_titles(
                db, model, user_id, deleted_through=cutoff, limit=limit
            )
            total.deleted += outcome.deleted
            total.failed.extend(outcome.failed)
        await db.commit()
    return total


async def purge_expired_media_titles(now: int | None = None) -> dict[str, int]:
    """Apply each account's opt-in media trash retention, within a bounded budget.

    A title becomes eligible once it has been in the trash for the chosen number
    of days, measured from its existing deletion time. Accounts without a
    choice, with Never, or with an unrecognised stored value are never touched.
    """
    current = int(time.time()) if now is None else now
    choices = [str(days) for days in MEDIA_TRASH_RETENTION_DAYS if days]
    async with SessionLocal() as db:
        accounts = (
            await db.scalars(
                select(UserPreferences.user_id)
                .where(UserPreferences.data[RETENTION_KEY].astext.in_(choices))
                .order_by(UserPreferences.user_id)
            )
        ).all()
    counts = {"accounts": 0, "purged_titles": 0, "failed_titles": 0}
    for user_id in accounts:
        budget = RETENTION_SWEEP_LIMIT - counts["purged_titles"] - counts["failed_titles"]
        if budget <= 0:
            break
        try:
            outcome = await _purge_account(user_id, current, budget)
        # One account's failed transaction must not stop the others; nothing of
        # that account was deleted, and the next sweep tries again.
        except SQLAlchemyError:
            logger.exception("Media trash retention failed for user %s", user_id)
            continue
        if outcome.deleted or outcome.failed:
            counts["accounts"] += 1
        counts["purged_titles"] += outcome.deleted
        counts["failed_titles"] += len(outcome.failed)
    if counts["purged_titles"] or counts["failed_titles"]:
        logger.info(
            "Media trash retention deleted %d title(s) for %d account(s); %d could not be deleted",
            counts["purged_titles"],
            counts["accounts"],
            counts["failed_titles"],
        )
    return counts
