"""Owned activity by day: the Statistics heatmap counts and its day-detail list.

Both use one day rule, so a square's count and the day it opens always agree:

* Episode and other history entries (``ActivityLog``) are stored as a calendar
  day with no time of day. They stay on that recorded day in every timezone and
  are never moved, because there is no time to convert.
* Achievements store their exact unlock instant. They belong to the local day
  that instant falls on in the requested IANA timezone (UTC when none is
  given). ``day_bounds`` gives that day's first and next-day first second, and
  both the heatmap and the day detail place unlocks with it.

Everything is scoped to the signed-in account. An activity row counts only
while it points at one of the caller's own titles that is not in Trash, and
achievements use the shared ``active_owned_games`` predicate, so another
account's rows, trashed titles and purged titles never count or link. Nothing
is invented: a day lists what was recorded, and episodes appear as the per-title
count the history log kept, not as guessed episode numbers."""

from bisect import bisect_right
from collections import Counter
from datetime import date, datetime, time, timedelta
from functools import lru_cache
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo, available_timezones

from sqlalchemy import Select, and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer, raiseload
from sqlalchemy.sql.functions import coalesce as sql_coalesce
from sqlalchemy.sql.functions import count as sql_count
from sqlalchemy.sql.functions import sum as sql_sum

from src.core.titles import display_title
from src.database.models.achievement import Achievement
from src.database.models.anime import Anime
from src.database.models.game import Game
from src.database.models.media_extras import ActivityEventType, ActivityLog, MediaType
from src.database.models.movies import Movie
from src.database.models.tv_show import TVShow
from src.features.game_stats import active_owned_games

DEFAULT_TIMEZONE = "UTC"
DAY_PAGE_LIMIT = 100
# The heatmap shows 52 weeks back from today, widened to whole Sunday-first weeks.
_WINDOW_DAYS = 364
# Far enough from both ends of the calendar that a day and its neighbours convert.
EARLIEST_DAY = date(1970, 1, 1)
LATEST_DAY = date(9998, 12, 31)
_DAY_SECONDS = 86400
_EPISODES = ActivityEventType.EPISODES_WATCHED
_ACTIVITY_MEDIA: tuple[tuple[MediaType, Any], ...] = (
    (MediaType.MOVIE, Movie),
    (MediaType.TV, TVShow),
    (MediaType.ANIME, Anime),
)
_MEDIA_DETAIL_FIELDS = ("description", "note", "provider_ids")


class ActivityTimezoneError(ValueError):
    """The requested timezone is not a known IANA name."""


@lru_cache(maxsize=1)
def _zone_names() -> frozenset[str]:
    # "localtime" is the server's own zone file, not a name a viewer can mean.
    return frozenset(available_timezones() - {"localtime"})


def resolve_timezone(name: str | None) -> ZoneInfo:
    """The viewer's IANA zone, or UTC when none is given."""
    if not name:
        return ZoneInfo(DEFAULT_TIMEZONE)
    if name not in _zone_names():
        raise ActivityTimezoneError("Choose a valid IANA timezone, such as Europe/London.")
    return ZoneInfo(name)


def local_day(timestamp: int, zone: ZoneInfo) -> date:
    return datetime.fromtimestamp(timestamp, zone).date()


def _day_start(day: date, zone: ZoneInfo) -> int:
    """The first whole second whose local date in ``zone`` is ``day``."""
    guess = int(datetime.combine(day, time.min, tzinfo=zone).timestamp())
    if local_day(guess, zone) == day and local_day(guess - 1, zone) < day:
        return guess
    # Midnight fell inside a daylight-saving gap; find the real first second.
    low, high = guess - _DAY_SECONDS, guess + _DAY_SECONDS
    while low + 1 < high:
        middle = (low + high) // 2
        if local_day(middle, zone) < day:
            low = middle
        else:
            high = middle
    return high


def day_bounds(day: date, zone: ZoneInfo) -> tuple[int, int]:
    """Unix seconds ``[start, end)`` of ``day`` in ``zone``; 23 or 25 hours across DST."""
    return _day_start(day, zone), _day_start(day + timedelta(days=1), zone)


def heatmap_window(zone: ZoneInfo, now: float | None = None) -> tuple[date, date]:
    """The first (a Sunday) and last (today) day the heatmap shows."""
    today = local_day(int(now), zone) if now is not None else datetime.now(zone).date()
    first = today - timedelta(days=_WINDOW_DAYS)
    return first - timedelta(days=(first.weekday() + 1) % 7), today


def owned_activity(statement: Select[Any], user_id: UUID) -> Select[Any]:
    """Join each history row to the caller's own active title it names.

    ``ActivityLog.media_id`` has no foreign key (it names one of three tables),
    so the join itself checks the owner and Trash. Rows for another account's
    title, a trashed title or a purged title drop out here."""
    for media_type, model in _ACTIVITY_MEDIA:
        statement = statement.outerjoin(
            model,
            and_(
                ActivityLog.media_type == media_type,
                model.id == ActivityLog.media_id,
                model.user_id == user_id,
                model.deleted_at.is_(None),
            ),
        )
    return statement.where(
        ActivityLog.user_id == user_id,
        or_(*(model.id.is_not(None) for _, model in _ACTIVITY_MEDIA)),
    )


def _unlocked_achievements(statement: Select[Any], user_id: UUID) -> Select[Any]:
    return statement.join(Game, Game.id == Achievement.game_id).where(
        active_owned_games(user_id),
        Achievement.unlocked.is_(True),
        Achievement.unlocked_at.is_not(None),
    )


def _activity_streaks(all_days: set[date], today: date) -> tuple[int, int]:
    longest = run = 0
    previous = None
    for d in sorted(all_days):
        run = run + 1 if previous and d - previous == timedelta(days=1) else 1
        longest = max(longest, run)
        previous = d
    current = 0
    cursor = today if today in all_days else today - timedelta(days=1)
    while cursor in all_days:
        current += 1
        cursor -= timedelta(days=1)
    return current, longest


async def _recorded_days(
    db: AsyncSession, user_id: UUID, first: date, last: date
) -> tuple[Counter[date], Counter[date]]:
    """Episodes watched and other history entries per recorded day in the window."""
    rows = (
        await db.execute(
            owned_activity(
                select(
                    ActivityLog.event_date,
                    ActivityLog.event_type,
                    sql_sum(ActivityLog.count),
                    sql_count(ActivityLog.id),
                ).select_from(ActivityLog),
                user_id,
            )
            .where(ActivityLog.event_date >= first, ActivityLog.event_date <= last)
            .group_by(ActivityLog.event_date, ActivityLog.event_type)
        )
    ).all()
    episodes: Counter[date] = Counter()
    history: Counter[date] = Counter()
    for day, event_type, total, entries in rows:
        if event_type == _EPISODES:
            episodes[day] += int(total)
        else:
            history[day] += int(entries)
    return episodes, history


def _day_row(day: date, episodes: int, achievements: int, history: int) -> dict[str, Any]:
    return {
        "date": day.isoformat(),
        "count": episodes + achievements,
        "episodes": episodes,
        "achievements": achievements,
        "history": history,
    }


# Windowed counts and streaks are assembled in one pass.
# pylint: disable=too-many-locals
async def activity_section(
    db: AsyncSession, user_id: UUID, zone: ZoneInfo, now: float | None = None
) -> dict[str, Any]:
    """Per-day counts for the heatmap window, plus all-time streaks.

    A day's count is the episodes the history log recorded plus the achievements
    unlocked on that local day; ``history`` counts other recorded entries (status
    changes, rewatches, ratings), which the day detail lists but the count does
    not include."""
    first, last = heatmap_window(zone, now)
    episodes, history = await _recorded_days(db, user_id, first, last)
    starts = [_day_start(first + timedelta(days=n), zone) for n in range((last - first).days + 2)]
    unlocked = (
        (await db.execute(_unlocked_achievements(select(Achievement.unlocked_at), user_id)))
        .scalars()
        .all()
    )
    achievements: Counter[date] = Counter()
    all_days = {local_day(ts, zone) for ts in unlocked if ts is not None}
    for ts in unlocked:
        if ts is not None and starts[0] <= ts < starts[-1]:
            achievements[first + timedelta(days=bisect_right(starts, ts) - 1)] += 1
    all_days |= {
        day
        for (day,) in (
            await db.execute(
                owned_activity(
                    select(ActivityLog.event_date).select_from(ActivityLog), user_id
                ).distinct()
            )
        ).all()
    }
    current, longest = _activity_streaks(all_days, last)
    per_day = [
        _day_row(day, episodes[day], achievements[day], history[day])
        for day in sorted(set(episodes) | set(achievements) | set(history))
    ]
    busiest = max(
        (row for row in per_day if row["count"]),
        key=lambda row: (row["count"], row["date"]),
        default=None,
    )
    return {
        "timezone": zone.key,
        "start": first.isoformat(),
        "end": last.isoformat(),
        "per_day": per_day,
        "active_days_total": len(all_days),
        "current_streak": current,
        "longest_streak": longest,
        "busiest_day": (
            {key: busiest[key] for key in ("date", "count", "episodes", "achievements")}
            if busiest
            else None
        ),
    }


def _page(position: int, size: int, offset: int, limit: int) -> tuple[int, int] | None:
    """The part of one list section (at ``position``) a page needs: (offset, limit)."""
    begin = max(offset, position)
    end = min(offset + limit, position + size)
    return (begin - position, end - begin) if begin < end else None


def _media_item(row: Any, language: str, kind: str) -> dict[str, Any]:
    entry, *titles = row
    title = next(item for item in titles if item is not None)
    item: dict[str, Any] = {
        "kind": kind,
        "media_type": entry.media_type.value,
        "id": str(entry.media_id),
        "title": display_title(title, language),
        "count": entry.count,
    }
    if kind == "history":
        item["event_type"] = entry.event_type.value
        item["detail"] = entry.detail
    return item


async def _recorded_page(
    db: AsyncSession, user_id: UUID, day: date, episodes: bool, window: tuple[int, int]
) -> list[Any]:
    models = [model for _, model in _ACTIVITY_MEDIA]
    sort_title = sql_coalesce(*(model.sort_title for model in models))
    statement = owned_activity(select(ActivityLog, *models).select_from(ActivityLog), user_id)
    order = (
        (ActivityLog.count.desc(), sort_title, ActivityLog.id)
        if episodes
        else (ActivityLog.event_type, sort_title, ActivityLog.id)
    )
    statement = (
        statement.options(
            *(
                defer(getattr(model, field), raiseload=True)
                for model in models
                for field in _MEDIA_DETAIL_FIELDS
            ),
            raiseload("*"),
        )
        .where(
            ActivityLog.event_date == day,
            (ActivityLog.event_type == _EPISODES)
            if episodes
            else (ActivityLog.event_type != _EPISODES),
        )
        .order_by(*order)
        .offset(window[0])
        .limit(window[1])
    )
    return list((await db.execute(statement)).all())


async def _achievement_page(
    db: AsyncSession, user_id: UUID, bounds: tuple[int, int], window: tuple[int, int]
) -> list[dict[str, Any]]:
    rows = (
        await db.execute(
            _unlocked_achievements(
                select(
                    Achievement.id,
                    Achievement.name,
                    Achievement.unlocked_at,
                    Game.id,
                    Game.title,
                ),
                user_id,
            )
            .where(Achievement.unlocked_at >= bounds[0], Achievement.unlocked_at < bounds[1])
            .order_by(Achievement.unlocked_at, Achievement.name, Achievement.id)
            .offset(window[0])
            .limit(window[1])
        )
    ).all()
    return [
        {
            "kind": "achievement",
            "id": str(achievement_id),
            "name": name,
            "unlocked_at": unlocked_at,
            "game_id": str(game_id),
            "game_title": game_title,
        }
        for achievement_id, name, unlocked_at, game_id, game_title in rows
    ]


async def _day_totals(
    db: AsyncSession, user_id: UUID, day: date, bounds: tuple[int, int]
) -> dict[str, int]:
    recorded = (
        await db.execute(
            owned_activity(
                select(
                    ActivityLog.event_type,
                    sql_sum(ActivityLog.count),
                    sql_count(ActivityLog.id),
                ).select_from(ActivityLog),
                user_id,
            )
            .where(ActivityLog.event_date == day)
            .group_by(ActivityLog.event_type)
        )
    ).all()
    totals = {"episodes": 0, "episode_titles": 0, "history": 0}
    for event_type, total, rows in recorded:
        if event_type == _EPISODES:
            totals["episodes"] += int(total)
            totals["episode_titles"] += int(rows)
        else:
            totals["history"] += int(rows)
    achievements = await db.scalar(
        _unlocked_achievements(select(sql_count(Achievement.id)), user_id).where(
            Achievement.unlocked_at >= bounds[0], Achievement.unlocked_at < bounds[1]
        )
    )
    return {**totals, "achievements": int(achievements or 0)}


async def activity_day(
    db: AsyncSession,
    user_id: UUID,
    day: date,
    zone: ZoneInfo,
    *,
    offset: int = 0,
    limit: int = 50,
    language: str = "english",
) -> dict[str, Any]:
    """One heatmap day: totals that match its square and a bounded page of entries.

    Entries come in a fixed order: titles with watched episodes (most first),
    achievements in unlock order, then other recorded history. ``offset`` and
    ``limit`` page through that combined list."""
    bounds = day_bounds(day, zone)
    totals = await _day_totals(db, user_id, day, bounds)
    sections = (
        ("episodes", totals["episode_titles"]),
        ("achievement", totals["achievements"]),
        ("history", totals["history"]),
    )
    items: list[dict[str, Any]] = []
    position = 0
    for kind, size in sections:
        window = _page(position, size, offset, limit)
        position += size
        if window is None:
            continue
        if kind == "achievement":
            items += await _achievement_page(db, user_id, bounds, window)
        else:
            rows = await _recorded_page(db, user_id, day, kind == "episodes", window)
            items += [_media_item(row, language, kind) for row in rows]
    return {
        "date": day.isoformat(),
        "timezone": zone.key,
        "starts_at": bounds[0],
        "ends_at": bounds[1],
        "count": totals["episodes"] + totals["achievements"],
        "episodes": totals["episodes"],
        "achievements": totals["achievements"],
        "history": totals["history"],
        "items": items,
        "total": position,
        "offset": offset,
        "limit": limit,
        "next_offset": max(offset, min(offset + limit, position)),
        "complete": offset + limit >= position,
    }


# pylint: enable=too-many-locals
