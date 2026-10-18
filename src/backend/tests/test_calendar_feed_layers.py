"""Per-media-kind calendar subscriptions share one token and the saved preferences."""

from datetime import date
from uuid import uuid4

import pytest
from fastapi import HTTPException

import src.api.routes.calendar_feed as feed_module
from src.api.routes.calendar_feed import (
    FEED_LAYERS,
    calendar_feed,
    calendar_layer_feed,
    get_feed_token,
    regenerate_feed_token,
)
from src.database.models.calendar_event import CalendarEvent
from src.database.models.user import User
from src.database.session import SessionLocal

WHEN = 1_800_000_000
IDS = {kind: uuid4() for kind in ("anime", "tv", "movie", "game")}


def _entries(show_estimated: bool = True) -> list[dict]:
    entries = [
        {
            "media_type": "anime",
            "media_id": IDS["anime"],
            "title": "Anime show",
            "next_episode_number": 3,
            "air_at": WHEN,
            "kind": "episode",
            "is_projected": False,
        },
        {
            "media_type": "anime",
            "media_id": IDS["anime"],
            "title": "Anime show",
            "next_episode_number": 4,
            "air_at": WHEN + 604800,
            "kind": "episode",
            "is_projected": True,
        },
        {
            "media_type": "tv",
            "media_id": IDS["tv"],
            "title": "TV show",
            "next_episode_number": 1,
            "air_at": WHEN,
            "kind": "episode",
            "is_projected": False,
        },
        {
            "media_type": "movie",
            "media_id": IDS["movie"],
            "title": "A, Movie; day",
            "next_episode_number": None,
            "air_at": WHEN,
            "kind": "release",
        },
        {
            "media_type": "game",
            "media_id": IDS["game"],
            "title": "Game",
            "next_episode_number": None,
            "air_at": WHEN,
            "kind": "release",
        },
    ]
    return entries


class _Result:
    def __init__(self, rows):
        self.rows = rows

    def scalars(self):
        return self

    def all(self):
        return self.rows


class _Db:
    def __init__(self, user, events=()):
        self.user = user
        self.events = list(events)

    async def scalar(self, _statement):
        return self.user

    async def execute(self, _statement):
        return _Result(self.events)


def _patch(monkeypatch, prefs=None):
    settings = {
        "calendar_game_releases": True,
        "calendar_hide_games": False,
        "calendar_show_estimated": True,
        **(prefs or {}),
    }
    seen = {}

    async def fake_prefs(_db, _user_id):
        return settings

    async def fake_entries(_db, _user_id, days, game_releases=False):
        seen["days"] = days
        seen["game_releases"] = game_releases
        rows = _entries()
        return rows if game_releases else [r for r in rows if r["media_type"] != "game"]

    monkeypatch.setattr(feed_module, "load_preferences", fake_prefs)
    monkeypatch.setattr(feed_module, "build_calendar_entries", fake_entries)
    return seen


def _user():
    return User(id=uuid4(), username="layer-test", email="layer@example.test", password_hash="x")


def _event():
    return CalendarEvent(
        id=uuid4(),
        user_id=uuid4(),
        title="Floating, note",
        event_date=date(2027, 1, 2),
        event_time="19:30",
    )


def _summaries(ics: str) -> list[str]:
    return [line for line in ics.split("\r\n") if line.startswith("SUMMARY:")]


def _uids(ics: str) -> set[str]:
    return {line.removeprefix("UID:") for line in ics.split("\r\n") if line.startswith("UID:")}


async def _layer(layer: str, db) -> str:
    return (await calendar_layer_feed("token", layer, db)).body.decode()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("layer", "expected"),
    [
        ("anime", {"Anime show (estimated)", "Anime show - Episode 3"}),
        ("tv", {"TV show - Episode 1"}),
        ("movies", {"A\\, Movie\\; day (release)"}),
        ("games", {"Game (release)"}),
        ("events", {"Floating\\, note"}),
    ],
)
async def test_each_layer_returns_only_its_own_kind(monkeypatch, layer, expected):
    _patch(monkeypatch)
    body = await _layer(layer, _Db(_user(), [_event()]))
    got = {line.removeprefix("SUMMARY:") for line in _summaries(body)}
    # an estimated anime row reads "... - Episode 4 (estimated)"
    got = {g.replace(" - Episode 4 (estimated)", " (estimated)") for g in got}
    assert got == expected


@pytest.mark.asyncio
async def test_combined_feed_still_has_everything_and_manual_events(monkeypatch):
    seen = _patch(monkeypatch)
    body = (await calendar_feed("token", _Db(_user(), [_event()]))).body.decode()
    assert seen == {"days": 90, "game_releases": True}
    assert len(_summaries(body)) == 6
    assert "X-WR-CALNAME:Media Tracker\r\n" in body
    assert "DTSTART:20270102T193000\r\n" in body  # floating, no Z or zone


@pytest.mark.asyncio
async def test_layer_feeds_name_the_calendar_and_keep_semantics(monkeypatch):
    _patch(monkeypatch)
    movies = await _layer("movies", _Db(_user()))
    assert "X-WR-CALNAME:Movies\r\n" in movies
    assert "DTSTART;VALUE=DATE:" in movies and "DTEND;VALUE=DATE:" in movies
    assert "SUMMARY:A\\, Movie\\; day (release)" in movies
    anime = await _layer("anime", _Db(_user()))
    assert "DTSTART:" in anime and "VALUE=DATE" not in anime
    assert "(estimated)" in anime
    events = await _layer("events", _Db(_user(), [_event()]))
    assert "X-WR-CALNAME:My entries\r\n" in events
    assert "DTSTART:20270102T193000\r\n" in events


@pytest.mark.asyncio
async def test_uids_are_the_same_in_combined_and_filtered_feeds(monkeypatch):
    _patch(monkeypatch)
    db = _Db(_user())
    combined = _uids((await calendar_feed("token", db)).body.decode())
    parts: set[str] = set()
    for layer in ("anime", "tv", "movies", "games"):
        layer_uids = _uids(await _layer(layer, db))
        assert layer_uids <= combined
        parts |= layer_uids
    assert parts == combined
    # and stable across refreshes
    assert _uids(await _layer("anime", db)) == _uids(await _layer("anime", db))


@pytest.mark.asyncio
async def test_layers_follow_saved_preferences(monkeypatch):
    _patch(monkeypatch, {"calendar_show_estimated": False, "calendar_hide_games": True})
    anime = await _layer("anime", _Db(_user()))
    assert "estimated" not in anime
    assert len(_summaries(anime)) == 1
    games = await _layer("games", _Db(_user()))
    assert not _summaries(games)
    assert "BEGIN:VCALENDAR" in games and "END:VCALENDAR" in games


@pytest.mark.asyncio
async def test_manual_events_are_not_in_the_media_layers(monkeypatch):
    _patch(monkeypatch)
    for layer in ("anime", "tv", "movies", "games"):
        assert "Floating" not in await _layer(layer, _Db(_user(), [_event()]))


@pytest.mark.asyncio
async def test_unknown_layer_is_not_found(monkeypatch):
    _patch(monkeypatch)
    with pytest.raises(HTTPException) as exc:
        await calendar_layer_feed("token", "music", _Db(_user()))
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_unknown_token_is_not_found_for_every_layer(monkeypatch):
    _patch(monkeypatch)
    for layer in FEED_LAYERS:
        with pytest.raises(HTTPException) as exc:
            await calendar_layer_feed("nope", layer, _Db(None))
        assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_token_paths_list_every_layer_and_regeneration_retires_them():
    async with SessionLocal() as db:
        user = User(
            username=f"lay_{uuid4().hex[:10]}",
            email=f"{uuid4().hex[:10]}@example.test",
            password_hash="x",
        )
        other = User(
            username=f"lay_{uuid4().hex[:10]}",
            email=f"{uuid4().hex[:10]}@example.test",
            password_hash="x",
        )
        db.add_all([user, other])
        await db.commit()
        await db.refresh(user)
        await db.refresh(other)
        try:
            first = await get_feed_token(db, user)
            assert first["path"].endswith(".ics")
            assert set(first["layers"]) == set(FEED_LAYERS)
            token = first["path"].split("/feed/", 1)[1].removesuffix(".ics")
            for layer, path in first["layers"].items():
                assert path == f"/api/calendar/feed/{token}/{layer}.ics"
                assert (await calendar_layer_feed(token, layer, db)).status_code == 200
            other_paths = await get_feed_token(db, other)
            assert other_paths["path"] != first["path"]

            regenerated = await regenerate_feed_token(db, user)
            new = regenerated["path"].split("/feed/", 1)[1].removesuffix(".ics")
            assert new != token
            for layer in FEED_LAYERS:
                with pytest.raises(HTTPException) as exc:
                    await calendar_layer_feed(token, layer, db)
                assert exc.value.status_code == 404
                assert (await calendar_layer_feed(new, layer, db)).status_code == 200
            # the other account's token never shows this account's feed
            other_token = other_paths["path"].split("/feed/", 1)[1].removesuffix(".ics")
            assert other_token != new
        finally:
            await db.delete(user)
            await db.delete(other)
            await db.commit()


@pytest.mark.asyncio
async def test_layer_feed_only_contains_the_token_owners_events():
    async with SessionLocal() as db:
        owner = User(
            username=f"lay_{uuid4().hex[:10]}",
            email=f"{uuid4().hex[:10]}@example.test",
            password_hash="x",
            calendar_token=uuid4().hex,
        )
        other = User(
            username=f"lay_{uuid4().hex[:10]}",
            email=f"{uuid4().hex[:10]}@example.test",
            password_hash="x",
            calendar_token=uuid4().hex,
        )
        db.add_all([owner, other])
        await db.flush()
        db.add(CalendarEvent(user_id=owner.id, title="Mine", event_date=date.today()))
        db.add(CalendarEvent(user_id=other.id, title="Theirs", event_date=date.today()))
        await db.commit()
        try:
            body = (await calendar_layer_feed(owner.calendar_token, "events", db)).body.decode()
            assert "Mine" in body
            assert "Theirs" not in body
        finally:
            await db.delete(owner)
            await db.delete(other)
            await db.commit()
