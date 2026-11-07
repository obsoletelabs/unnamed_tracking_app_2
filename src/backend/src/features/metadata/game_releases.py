"""Release dates a metadata refresh stores on a game (Plugin API 1.1.20, CONTRACTS-v1 C5.4).

``games.release_date`` stays a date: for a year or a month it is the first day of the period, so
sorting and the release-year statistics keep working, and ``release_date_precision`` says how
much of it is known (NULL is a day, as every date stored before 1.1.20 is). ``games.releases``
holds up to five provider-reported releases and, like ``provider_custom``, is replaced by a
refresh. The ``release_date`` lock covers the date and its precision; ``releases`` has its own.
"""

from collections.abc import Collection
from datetime import date
from typing import Any

from sqlalchemy import ColumnElement, or_

from src.database.models.game import Game
from src.plugin_api.metadata_contracts import MetadataRelease, partial_date_start
from src.plugin_api.vocab import FIRST_RELEASE_CATEGORIES

MAX_RELEASES = 5
RELEASES_LOCK = "releases"


def _legacy_day(raw: object) -> date | None:
    if isinstance(raw, date):
        return raw
    try:
        return date.fromisoformat(str(raw)[:10]) if raw else None
    except ValueError:
        return None


def release_source(match: dict[str, Any]) -> str:
    """The result field ``release_of(match)`` takes its date from: ``first_release``, or the
    legacy ``release_date`` (also when that day refines a reported year or month). The fields
    of a merged result can come from different providers, so a refresh records the release
    date as the one this field came from."""
    first = match.get("first_release")
    legacy = _legacy_day(match.get("release_date"))
    if not isinstance(first, dict) or legacy is None:
        return "first_release" if isinstance(first, dict) else "release_date"
    day, precision = release_of(match)
    return "release_date" if day == legacy and precision is None else "first_release"


def release_of(match: dict[str, Any]) -> tuple[date | None, str | None]:
    """The (date, precision) a refresh result reports: its ``first_release`` when it has one,
    otherwise the day-only ``release_date`` every older provider sends. A day is precision None.

    Fields of a merged result can come from different providers. A day that falls inside the
    reported year or month agrees with it and is more precise, so it wins (as in Atlas's
    hierarchical vote, C5.2).

    A first release is a ``full`` or ``early_access`` release (C5.1); one of another category
    (a beta, a demo) is not the game's release date and is ignored."""
    legacy = _legacy_day(match.get("release_date"))
    first = match.get("first_release")
    if isinstance(first, dict):
        try:
            release = MetadataRelease.model_validate(first)
        except ValueError:
            release = None
        if release is not None and release.category not in FIRST_RELEASE_CATEGORIES:
            release = None
        if release is not None and release.precision != "day":
            if legacy is not None and legacy.isoformat().startswith(release.date + "-"):
                return legacy, None
            return partial_date_start(release.date), release.precision
        if release is not None:
            return partial_date_start(release.date), None
    return legacy, None


# how coarse a precision is; NULL is a day
_COARSENESS = {None: 0, "day": 0, "month": 1, "year": 2}


def _within(day: date, start: date, precision: str | None) -> bool:
    """Whether ``day`` falls inside the period that starts at ``start``."""
    if precision == "year":
        return day.year == start.year
    if precision == "month":
        return (day.year, day.month) == (start.year, start.month)
    return day == start


def release_for(game: Game, match: dict[str, Any]) -> tuple[date | None, str | None]:
    """The (date, precision) a refresh should store on ``game``: ``release_of(match)``, unless
    the game already holds a more precise date inside the reported period.

    A stored day agrees with a reported year (or month) that contains it and knows more, so it
    is kept (C5.2's consistency rule applied across refreshes, not only inside one result); a
    stored date outside the period is replaced as before."""
    day, precision = release_of(match)
    stored, stored_precision = game.release_date, game.release_date_precision
    if (
        day is not None
        and stored is not None
        and _COARSENESS.get(stored_precision, 0) < _COARSENESS.get(precision, 0)
        and _within(stored, day, precision)
    ):
        return stored, stored_precision
    return day, precision


def known_day() -> ColumnElement[bool]:
    """Games whose ``release_date`` is a known day, not the first day of a year or a month.

    Server-built feeds that place a game on one day (the calendar, release notifications) use
    it, so a year-only "2027" is never shown as a release on 1 January (C5.5)."""
    return or_(Game.release_date_precision.is_(None), Game.release_date_precision == "day")


def valid_releases(raw: object) -> list[dict[str, Any]] | None:
    """The entries of ``raw`` a refresh would store: each a valid ``MetadataRelease``, the first
    of each (platform, region, edition, category), at most five. None when none is left."""
    if not isinstance(raw, list) or not raw:
        return None
    rows: list[dict[str, Any]] = []
    keys: set[tuple[str | None, str | None, str | None, str]] = set()
    for entry in raw:
        try:
            release = MetadataRelease.model_validate(entry)
        except ValueError:
            continue
        if release.key in keys:
            continue
        keys.add(release.key)
        rows.append(release.model_dump(mode="json"))
        if len(rows) == MAX_RELEASES:
            break
    return rows or None


def precision_for_date(day: date | None, precision: str | None) -> str | None:
    """``precision`` when it can describe ``day``: a year or month whose first day ``day`` is,
    within the C5.1 year range. Anything else is a day (None), since the date is what a person
    sees and edits (an imported or hand-edited pair that disagree keeps the date)."""
    if day is None or precision not in ("year", "month"):
        return None
    try:
        release = MetadataRelease.model_validate(
            {"date": day.isoformat()[: 4 if precision == "year" else 7], "precision": precision}
        )
    except ValueError:
        return None
    return precision if partial_date_start(release.date) == day else None


def _releases(match: dict[str, Any]) -> list[dict[str, Any]] | None:
    return valid_releases(match.get("releases"))


def add_release_updates(
    game: Game,
    match: dict[str, Any],
    updates: dict[str, object],
    skipped_locked: list[str],
    *,
    save_release_date: bool,
    held_by_preference: Collection[str] = (),
) -> None:
    """Add the precision and releases a refresh should store to ``updates``.

    Runs after the release date itself was considered. A changed date resets the stored
    precision (see ``Game``), so the precision is written whenever the date is, and whenever
    only the precision differs ("2011" becoming "2011-01-01")."""
    if not save_release_date:
        return
    day, precision = release_for(game, match)
    date_is_free = (
        "release_date" not in (game.locked_fields or [])
        and "release_date" not in held_by_preference
    )
    if (
        day is not None
        and date_is_free
        and (
            "release_date" in updates
            or (day == game.release_date and precision != game.release_date_precision)
        )
    ):
        updates["release_date_precision"] = precision
    releases = _releases(match)
    if releases is None:
        return
    if RELEASES_LOCK in (game.locked_fields or []):
        skipped_locked.append(RELEASES_LOCK)
    elif releases != game.releases:
        updates["releases"] = releases


def sync_release_updates(game: Game, match: dict[str, Any]) -> dict[str, object]:
    """The release date, precision and releases a library sync stores on a game it enriches:
    the same values a refresh stores (so a 1.1.20 ``first_release`` arrives with the game), with
    locks honoured by ``apply_metadata_updates`` and ``add_release_updates``."""
    updates: dict[str, object] = {}
    day = release_for(game, match)[0]
    if day is not None and day != game.release_date:
        updates["release_date"] = day
    add_release_updates(game, match, updates, [], save_release_date=True)
    return updates
