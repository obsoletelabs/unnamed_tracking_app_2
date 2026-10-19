"""iCalendar (.ics) feed of the calendar, subscribable from Google/Apple
Calendar. Calendar apps fetch by plain URL and can't send a login
cookie, so the feed lives at a secret per-user token path instead of
behind the normal session auth."""

import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.routes.media_extras import build_calendar_entries
from src.core.auth import get_current_user
from src.core.preferences import load_preferences
from src.database.models.calendar_event import CalendarEvent
from src.database.models.user import User
from src.database.session import get_db

authed_router = APIRouter(
    prefix="/api/calendar", tags=["calendar"], dependencies=[Depends(get_current_user)]
)
public_router = APIRouter(prefix="/api/calendar", tags=["calendar"])

_FEED_DAYS = 90
# Subscriptions beyond the combined feed: one per kind of media, plus the user's own events.
# Every one uses the same secret token, so regenerating it ends all of them at once.
FEED_KINDS = ("anime", "tv", "movie", "game", "manual")
_FEED_LABELS = {
    "anime": "Anime",
    "tv": "TV shows",
    "movie": "Movies",
    "game": "Games",
    "manual": "My events",
}
# A colour *hint*: Google, Apple and Outlook decide for themselves how, or whether, to use it.
_FEED_COLORS = {
    "anime": ("hotpink", "#FF69B4"),
    "tv": ("royalblue", "#4169E1"),
    "movie": ("orange", "#FFA500"),
    "game": ("seagreen", "#2E8B57"),
    "manual": ("gray", "#808080"),
}


def _ics_escape(text: str) -> str:
    return (
        text.replace("\r", "")
        .replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
    )


def _feed_paths(token: str) -> dict[str, str]:
    base = f"/api/calendar/feed/{token}"
    return {"combined": f"{base}.ics", **{kind: f"{base}/{kind}.ics" for kind in FEED_KINDS}}


def _stamp(unix: int) -> str:
    return datetime.fromtimestamp(unix, tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")


# One serializer for the combined feed and every layer; its locals are the iCalendar properties.
# pylint: disable-next=too-many-locals
def _build_ics(
    entries: list[dict], events: list[CalendarEvent] | None = None, kind: str | None = None
) -> str:
    name = "Media Tracker" if kind is None else f"Media Tracker - {_FEED_LABELS[kind]}"
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Media Tracker//Calendar//EN",
        "CALSCALE:GREGORIAN",
        f"X-WR-CALNAME:{name}",
    ]
    if kind is not None:
        css, hexcode = _FEED_COLORS[kind]
        lines += [f"COLOR:{css}", f"X-APPLE-CALENDAR-COLOR:{hexcode}"]
    now = _stamp(int(datetime.now(tz=timezone.utc).timestamp()))
    for e in entries:
        ep = f" - Episode {e['next_episode_number']}" if e["next_episode_number"] else ""
        estimated = " (estimated)" if e.get("is_projected") else ""
        uid = f"{e['media_type']}-{e['media_id']}-{e['kind']}-{e['next_episode_number'] or 0}@media-tracker"
        lines.append("BEGIN:VEVENT")
        lines.append(f"UID:{uid}")
        lines.append(f"DTSTAMP:{now}")
        if e["kind"] == "release":
            # a release date has no real time-of-day: an all-day event
            day = datetime.fromtimestamp(e["air_at"], tz=timezone.utc).strftime("%Y%m%d")
            end = (
                datetime.fromtimestamp(e["air_at"], tz=timezone.utc) + timedelta(days=1)
            ).strftime("%Y%m%d")
            lines.append(f"DTSTART;VALUE=DATE:{day}")
            lines.append(f"DTEND;VALUE=DATE:{end}")
            lines.append(f"SUMMARY:{_ics_escape(e['title'] + ' (release)')}")
        else:
            lines.append(f"DTSTART:{_stamp(e['air_at'])}")
            lines.append(f"DTEND:{_stamp(e['air_at'] + 30 * 60)}")
            lines.append(f"SUMMARY:{_ics_escape(e['title'] + ep + estimated)}")
        lines.append("END:VEVENT")
    for ev in events or []:
        lines.append("BEGIN:VEVENT")
        lines.append(f"UID:manual-{ev.id}@media-tracker")
        lines.append(f"DTSTAMP:{now}")
        day = ev.event_date.strftime("%Y%m%d")
        if ev.event_time:
            # a floating local time: no zone is stored, so the calendar app
            # shows it at that clock time wherever the viewer is
            stamp = f"{day}T{ev.event_time.replace(':', '')}00"
            lines.append(f"DTSTART:{stamp}")
        else:
            end = (ev.event_date + timedelta(days=1)).strftime("%Y%m%d")
            lines.append(f"DTSTART;VALUE=DATE:{day}")
            lines.append(f"DTEND;VALUE=DATE:{end}")
        lines.append(f"SUMMARY:{_ics_escape(ev.title)}")
        if ev.note:
            lines.append(f"DESCRIPTION:{_ics_escape(ev.note)}")
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


@authed_router.get("/feed-token")
async def get_feed_token(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """The secret path for this user's .ics feed, created on first ask.

    `path` is the combined feed that has always been offered; `feeds` also lists one feed per
    kind of media and one for the user's own events."""
    if not current_user.calendar_token:
        current_user.calendar_token = secrets.token_urlsafe(32)
        await db.commit()
    return {
        "path": f"/api/calendar/feed/{current_user.calendar_token}.ics",
        "feeds": _feed_paths(current_user.calendar_token),
    }


@authed_router.post("/feed-token/regenerate")
async def regenerate_feed_token(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Invalidates every old feed URL (e.g. after sharing one by accident)."""
    current_user.calendar_token = secrets.token_urlsafe(32)
    await db.commit()
    return {
        "path": f"/api/calendar/feed/{current_user.calendar_token}.ics",
        "feeds": _feed_paths(current_user.calendar_token),
    }


async def _feed_response(token: str, db: AsyncSession, kind: str | None) -> Response:
    """The combined feed (kind None) or one kind's feed; both follow the saved calendar
    preferences and both are only reachable through the owner's current secret token."""
    user = await db.scalar(
        select(User).where(User.calendar_token == token, User.is_active.is_(True))
    )
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown calendar feed")
    prefs = await load_preferences(db, user.id)
    entries: list[dict] = []
    if kind != "manual":
        entries = await build_calendar_entries(
            db,
            user.id,
            _FEED_DAYS,
            game_releases=bool(prefs["calendar_game_releases"])
            and not prefs["calendar_hide_games"],
        )
        if not prefs["calendar_show_estimated"]:
            entries = [e for e in entries if not e.get("is_projected")]
        if kind is not None:
            entries = [e for e in entries if e["media_type"] == kind]
    events: list[CalendarEvent] = []
    if kind is None or kind == "manual":
        events = list(
            (await db.execute(select(CalendarEvent).where(CalendarEvent.user_id == user.id)))
            .scalars()
            .all()
        )
    return Response(
        content=_build_ics(entries, events, kind), media_type="text/calendar; charset=utf-8"
    )


@public_router.get("/feed/{token}.ics")
async def calendar_feed(token: str, db: AsyncSession = Depends(get_db)) -> Response:
    return await _feed_response(token, db, None)


@public_router.get("/feed/{token}/{kind}.ics")
async def calendar_feed_for_kind(
    token: str, kind: str, db: AsyncSession = Depends(get_db)
) -> Response:
    """One layer of the calendar: `anime`, `tv`, `movie`, `game` or `manual` (your own events)."""
    if kind not in FEED_KINDS:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown calendar feed")
    return await _feed_response(token, db, kind)
