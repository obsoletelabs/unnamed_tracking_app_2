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

# The separate subscriptions. Each follows the same saved calendar preferences
# as the combined feed (airing statuses, estimates, games). Only the combined
# feed and "events" carry the user's own manual entries, which are not tied to
# one media kind. Every URL shares the one secret token, so regenerating it
# retires all of them at once.
FEED_LAYERS: dict[str, tuple[str, str]] = {
    # layer -> (media_type in the entries, calendar name)
    "anime": ("anime", "Anime"),
    "tv": ("tv", "TV"),
    "movies": ("movie", "Movies"),
    "games": ("game", "Games"),
    "events": ("", "My entries"),
}


def _ics_escape(text: str) -> str:
    return (
        text.replace("\r", "")
        .replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
    )


def _stamp(unix: int) -> str:
    return datetime.fromtimestamp(unix, tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _build_ics(
    entries: list[dict],
    events: list[CalendarEvent] | None = None,
    calendar_name: str = "Media Tracker",
) -> str:
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Media Tracker//Calendar//EN",
        "CALSCALE:GREGORIAN",
        f"X-WR-CALNAME:{_ics_escape(calendar_name)}",
    ]
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


def _feed_paths(token: str) -> dict:
    """`path` is the combined feed (unchanged); `layers` the per-kind ones."""
    return {
        "path": f"/api/calendar/feed/{token}.ics",
        "layers": {layer: f"/api/calendar/feed/{token}/{layer}.ics" for layer in FEED_LAYERS},
    }


@authed_router.get("/feed-token")
async def get_feed_token(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """The secret paths for this user's .ics feeds, created on first ask."""
    if not current_user.calendar_token:
        current_user.calendar_token = secrets.token_urlsafe(32)
        await db.commit()
    return _feed_paths(current_user.calendar_token)


@authed_router.post("/feed-token/regenerate")
async def regenerate_feed_token(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Invalidates every old feed URL (e.g. after sharing one by accident)."""
    current_user.calendar_token = secrets.token_urlsafe(32)
    await db.commit()
    return _feed_paths(current_user.calendar_token)


async def _feed_response(db: AsyncSession, token: str, layer: str | None) -> Response:
    user = await db.scalar(
        select(User).where(User.calendar_token == token, User.is_active.is_(True))
    )
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown calendar feed")
    media_type, name = FEED_LAYERS[layer] if layer else ("", "Media Tracker")
    prefs = await load_preferences(db, user.id)
    games_on = bool(prefs["calendar_game_releases"]) and not prefs["calendar_hide_games"]
    entries = await build_calendar_entries(
        db,
        user.id,
        _FEED_DAYS,
        game_releases=games_on,
    )
    if not prefs["calendar_show_estimated"]:
        entries = [e for e in entries if not e.get("is_projected")]
    if layer:
        entries = [e for e in entries if media_type and e["media_type"] == media_type]
    events: list[CalendarEvent] = []
    if layer in (None, "events"):
        events = list(
            (await db.execute(select(CalendarEvent).where(CalendarEvent.user_id == user.id)))
            .scalars()
            .all()
        )
    return Response(
        content=_build_ics(entries, events, name),
        media_type="text/calendar; charset=utf-8",
    )


@public_router.get("/feed/{token}.ics")
async def calendar_feed(token: str, db: AsyncSession = Depends(get_db)) -> Response:
    return await _feed_response(db, token, None)


@public_router.get("/feed/{token}/{layer}.ics")
async def calendar_layer_feed(
    token: str, layer: str, db: AsyncSession = Depends(get_db)
) -> Response:
    if layer not in FEED_LAYERS:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown calendar feed")
    return await _feed_response(db, token, layer)
