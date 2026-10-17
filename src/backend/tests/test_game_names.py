"""Name quality, provenance and placeholder repair (#98)."""

from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import func, select, update

from src.core.auth import get_current_user
from src.database.models.game import Game
from src.database.models.game_identity import GameSourceObservation
from src.database.models.game_identity_tracking import FieldQuality, GameFieldValue
from src.database.session import SessionLocal
from src.features.game_identity import names
from src.features.game_ingestion import legacy
from src.features.imports.library_games import get_or_create_game
from src.main import app
from tests.test_game_ingestion import ingest, make_game, users  # noqa: F401

PLACEHOLDERS = [
    "Steam App 12345",
    "Steam app 220",
    "steam_app_7",
    "AppID 12345",
    "AppID: 12345",
    "Unknown Game",
    "  unknown   game ",
    "Untitled",
    "Untitled Game",
    "Game 12345",
]
LEGITIMATE = [
    "Untitled Goose Game",
    "Unknown 9",
    "Game 2",
    "Game of Thrones",
    "Steam Hearts",
    "AppID Quest",
    "Appid",
    "Steamworld Dig",
    "The Unknown Game of Chance",
    "12 Minutes",
    "Portal 2",
    "N",
    "Ø",
]


@pytest.mark.parametrize("title", PLACEHOLDERS)
def test_recognised_placeholders(title):
    assert names.classify_title(title, source="Steam") == FieldQuality.PLACEHOLDER


@pytest.mark.parametrize("title", LEGITIMATE)
def test_legitimate_unusual_titles_are_not_placeholders(title):
    assert names.classify_title(title, source="Steam") == FieldQuality.STANDARD


def test_low_quality_and_provider_awareness():
    assert names.classify_title("") == FieldQuality.LOW
    assert names.classify_title("12345") == FieldQuality.LOW
    assert names.classify_title("12345", external_id="12345") == FieldQuality.PLACEHOLDER
    assert names.classify_title("App 440", source="Steam") == FieldQuality.PLACEHOLDER
    assert names.classify_title("App 440", source="GOG") == FieldQuality.STANDARD


def test_precedence_matrix():
    q = FieldQuality
    assert names.decide_title(q.PLACEHOLDER, False, q.STANDARD).action == "replace"
    assert names.decide_title(q.PLACEHOLDER, False, q.CANONICAL).action == "replace"
    assert names.decide_title(q.STANDARD, False, q.PLACEHOLDER).action == "keep"
    assert names.decide_title(q.CANONICAL, False, q.LOW).action == "keep"
    assert names.decide_title(q.STANDARD, True, q.STANDARD).action == "propose"
    assert names.decide_title(q.PLACEHOLDER, True, q.CANONICAL).action == "propose"
    assert names.decide_title(q.STANDARD, True, q.PLACEHOLDER).action == "keep"
    assert names.decide_title(q.CANONICAL, False, q.STANDARD).action == "propose"
    assert names.decide_title(q.STANDARD, False, q.CANONICAL).action == "replace"
    assert names.decide_title(q.STANDARD, False, q.STANDARD, same=True).action == "unchanged"


async def sync(user, external_id, title, source="Steam", game=None, scope=None):
    """One Steam-style sync of a record, the way library_sync does it."""
    async with SessionLocal() as db:
        game, created = await get_or_create_game(db, user, title, source, external_id=external_id)
        await legacy.record_observations(
            db,
            user,
            legacy.STEAM,
            [(game.id, legacy.build_observation(external_id, title=title))],
        )
        await db.commit()
        return game.id, created


async def title_of(game_id):
    async with SessionLocal() as db:
        return await db.scalar(select(Game.title).where(Game.id == game_id))


async def values(game_id):
    async with SessionLocal() as db:
        return (
            await db.scalars(
                select(GameFieldValue)
                .where(GameFieldValue.game_id == game_id)
                .order_by(GameFieldValue.recorded_at, GameFieldValue.state)
            )
        ).all()


async def game_count(user):
    async with SessionLocal() as db:
        return await db.scalar(select(func.count()).select_from(Game).where(Game.user_id == user))


async def lock_title(game_id):
    async with SessionLocal() as db:
        await db.execute(update(Game).where(Game.id == game_id).values(locked_fields=["title"]))
        await db.commit()


async def test_wishlist_placeholder_is_upgraded_without_a_new_entry(users):  # noqa: F811
    user = await users()
    wished, created = await sync(user, "620", "Steam app 620")
    assert created
    async with SessionLocal() as db:
        game = await db.get(Game, wished)
        game.playtime_seconds = 90
        await db.commit()
    same, created = await sync(user, "620", "Portal 2")
    assert (same, created) == (wished, False)
    assert await title_of(wished) == "Portal 2"
    assert await game_count(user) == 1
    async with SessionLocal() as db:
        game = await db.get(Game, wished)
        assert (game.external_id, game.playtime_seconds, game.sort_title) == ("620", 90, "portal 2")
    rows = {(v.state, v.value): v for v in await values(wished)}
    assert rows[("current", "Portal 2")].quality == "standard"
    assert rows[("current", "Portal 2")].origin == "source"
    assert rows[("current", "Portal 2")].observation_id is not None
    assert rows[("superseded", "Steam app 620")].quality == "placeholder"


async def test_a_later_low_quality_name_does_not_replace_a_good_one(users):  # noqa: F811
    user = await users()
    game_id, _ = await sync(user, "620", "Portal 2")
    await sync(user, "620", "Steam App 620")
    await sync(user, "620", "Unknown Game")
    assert await title_of(game_id) == "Portal 2"
    async with SessionLocal() as db:
        raw = await db.scalar(
            select(GameSourceObservation.observed_title).where(
                GameSourceObservation.user_id == user
            )
        )
    assert raw == "Unknown Game"  # the raw source name is preserved for audit
    assert not [v for v in await values(game_id) if v.state == "proposed"]


async def test_user_locked_name_gets_a_review_not_a_silent_rename(users):  # noqa: F811
    user = await users()
    game_id, _ = await sync(user, "620", "Portal 2")
    await lock_title(game_id)
    await sync(user, "620", "Portal 2: Remastered")
    await sync(user, "620", "Portal 2: Remastered")  # replay: still one open review
    assert await title_of(game_id) == "Portal 2"
    async with SessionLocal() as db:
        pending = await names.pending_reviews(db, user)
        assert [(v.value, g.title) for v, g in pending] == [("Portal 2: Remastered", "Portal 2")]
        observations = await db.scalar(
            select(func.count())
            .select_from(GameSourceObservation)
            .where(GameSourceObservation.user_id == user)
        )
    assert observations == 1 and await game_count(user) == 1
    # a placeholder never raises a review for a locked name
    await sync(user, "620", "Steam App 620")
    async with SessionLocal() as db:
        assert len(await names.pending_reviews(db, user)) == 1


async def test_update_and_keep_current_actions(users):  # noqa: F811
    user = await users()
    game_id, _ = await sync(user, "1", "Alpha")
    await lock_title(game_id)
    await sync(user, "1", "Beta")
    async with SessionLocal() as db:
        ((review, _),) = await names.pending_reviews(db, user)
        await names.keep_review(db, user, review.id)
        await db.commit()
    await sync(user, "1", "Beta")  # the same answered name is not offered again
    async with SessionLocal() as db:
        assert await names.pending_reviews(db, user) == []
    await sync(user, "1", "Gamma")
    async with SessionLocal() as db:
        ((review, _),) = await names.pending_reviews(db, user)
        game = await names.accept_review(db, user, review.id)
        await db.commit()
        assert (game.title, game.sort_title, game.locked_fields) == ("Gamma", "gamma", ["title"])
    current = [v for v in await values(game_id) if v.state == "current"]
    assert [(v.value, v.locked) for v in current] == [("Gamma", True)]


async def test_existing_wrongly_named_games_are_repaired_safely(users):  # noqa: F811
    user = await users()
    game_id = await make_game(user, "Steam App 400")
    other = await make_game(user, "Unknown Game")
    locked = await make_game(user, "Steam App 500")
    await lock_title(locked)
    async with SessionLocal() as db:
        for gid, ext in ((game_id, "400"), (locked, "500")):
            game = await db.get(Game, gid)
            game.source, game.external_id, game.playtime_seconds = "Steam", ext, 120
        await db.commit()
    await ingest(user, {"external_id": "400", "title": "Portal"}, attach={"400": game_id})
    await ingest(user, {"external_id": "500", "title": "Left 4 Dead"}, attach={"500": locked})
    async with SessionLocal() as db:
        dry = await names.repair_games(db, user, dry_run=True)
        assert (dry.replaced, dry.proposed, dry.unresolved) == ([game_id], [locked], [other])
        await db.rollback()
    assert await title_of(game_id) == "Steam App 400"
    async with SessionLocal() as db:
        report = await names.repair_games(db, user)
        await db.commit()
    assert (report.replaced, report.proposed, report.unresolved) == ([game_id], [locked], [other])
    assert await title_of(game_id) == "Portal"
    assert await title_of(locked) == "Steam App 500"
    assert await title_of(other) == "Unknown Game"
    assert await game_count(user) == 3
    async with SessionLocal() as db:
        game = await db.get(Game, game_id)
        assert (game.external_id, game.playtime_seconds, game.source) == ("400", 120, "Steam")
        assert len(await names.pending_reviews(db, user)) == 1
        again = await names.repair_games(db, user)
        await db.commit()
    assert again.replaced == [] and again.proposed == []
    assert [v.state for v in await values(game_id)].count("current") == 1


async def test_playnite_ids_and_source_names_survive_a_repair(users):  # noqa: F811
    user = await users()
    guid = uuid4()
    game_id = await make_game(user, "Unknown Game")
    async with SessionLocal() as db:
        await db.execute(update(Game).where(Game.id == game_id).values(playnite_guid=guid))
        await legacy.record_playnite_fields(db, user, game_id, guid, {"title": "Hollow Knight"})
        await db.commit()
    assert await title_of(game_id) == "Hollow Knight"
    async with SessionLocal() as db:
        game = await db.get(Game, game_id)
        observation = await db.scalar(
            select(GameSourceObservation).where(GameSourceObservation.user_id == user)
        )
        assert game.playnite_guid == guid
        assert (observation.external_id, observation.observed_title) == (str(guid), "Hollow Knight")
    assert await game_count(user) == 1


async def test_trusted_metadata_upgrades_placeholders_only():
    placeholder = Game(title="Steam app 70", sort_title="steam app 70", source="Steam")
    placeholder.locked_fields = []
    assert names.apply_candidate_title(placeholder, "Half-Life", trusted_metadata=True).action == (
        "replace"
    )
    assert (placeholder.title, placeholder.sort_title) == ("Half-Life", "half-life")
    assert names.apply_candidate_title(placeholder, "Unknown Game").action == "keep"
    locked = Game(title="Steam app 70", sort_title="x", source="Steam", locked_fields=["title"])
    assert names.apply_candidate_title(locked, "Half-Life", trusted_metadata=True).action == (
        "propose"
    )
    assert locked.title == "Steam app 70"


@pytest.fixture
async def api():
    holder = {}

    def make(user_id):
        holder["id"] = user_id
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=holder["id"])
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        )

    yield make
    app.dependency_overrides.pop(get_current_user, None)


async def test_http_review_repair_and_provenance(users, api):  # noqa: F811
    user, other = await users(), await users()
    game_id, _ = await sync(user, "9", "Alpha")
    await lock_title(game_id)
    await sync(user, "9", "Beta")
    async with api(user) as client:
        found = (await client.get("/api/game-identity/names/reviews")).json()
        assert [(r["current_title"], r["proposed_title"]) for r in found] == [("Alpha", "Beta")]
        info = (await client.get(f"/api/game-identity/names/games/{game_id}")).json()
        assert info["locked"] and info["title"] == "Alpha"
        assert info["source_names"][0]["title"] == "Beta"
        assert (await client.post("/api/game-identity/names/repair", json={})).json()[
            "dry_run"
        ] is False
    async with api(other) as client:
        review = found[0]["id"]
        assert (
            await client.post(f"/api/game-identity/names/reviews/{review}/update")
        ).status_code == 404
        assert (await client.get(f"/api/game-identity/names/games/{game_id}")).status_code == 404
    async with api(user) as client:
        done = await client.post(f"/api/game-identity/names/reviews/{review}/update")
        assert done.json()["title"] == "Beta"
        assert (await client.get("/api/game-identity/names/reviews")).json() == []
        assert (
            await client.post(f"/api/game-identity/names/reviews/{review}/keep")
        ).status_code == 404


async def test_a_playnite_rename_on_create_retry_is_reviewed_not_applied(users):  # noqa: F811
    user = await users()
    guid = uuid4()
    game_id = await make_game(user, "Hades")
    async with SessionLocal() as db:
        await db.execute(update(Game).where(Game.id == game_id).values(playnite_guid=guid))
        await legacy.record_playnite_fields(db, user, game_id, guid, {"title": "Hades"})
        await legacy.record_playnite_fields(db, user, game_id, guid, {"title": "Hades (2020)"})
        await db.commit()
        assert await title_of(game_id) == "Hades"
        ((review, _),) = await names.pending_reviews(db, user)
        assert review.value == "Hades (2020)"
        game = await names.accept_review(db, user, review.id)
        await db.commit()
        assert (game.title, game.playnite_guid) == ("Hades (2020)", guid)
