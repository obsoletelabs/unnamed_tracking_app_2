"""Provider-neutral review preserves explicit keep-both decisions and user scope."""

import asyncio
from datetime import date
from uuid import uuid4

import pytest
from sqlalchemy import delete, event, select

from src.database.models.game import Game
from src.database.models.game_duplicate_dismissal import GameDuplicateDismissal
from src.database.models.user import User
from src.database.session import SessionLocal
from src.features.game_duplicates import match_key, suggestions
from src.plugin_api.game_import import dispatch_game_import
from tests.test_game_files_flow import game_flow  # noqa: F401


async def add_game(user_id, title="Flow", **fields):
    async with SessionLocal() as db:
        game = Game(
            user_id=user_id,
            title=title,
            sort_title=title.casefold(),
            folder_location=uuid4().hex,
            **fields,
        )
        db.add(game)
        await db.commit()
        return game.id


def test_matching_preserves_unicode_editions_and_unrelated_titles():
    assert match_key("Baldur’s Gate™") == match_key("BALDUR'S GATE")
    assert match_key("ポータル") and match_key("ポータル") != match_key("ゼルダ")
    assert match_key("Prey") != match_key("Prey: Deluxe Edition")
    assert match_key("Pokémon") != match_key("Pokemon")
    assert match_key("™® --") == ""


@pytest.mark.parametrize("source", ["Steam", "Epic Games", "GOG", "Plugin store"])
async def test_reviews_include_all_import_sources_without_adoption(flow, source):
    imported = await add_game(flow.user_id, "Flow™", source=source, external_id="42")
    result = await flow.client.get("/api/game-duplicates")
    assert result.status_code == 200
    pairs = result.json()["pairs"]
    assert len(pairs) == 1
    assert {pairs[0]["first"]["id"], pairs[0]["second"]["id"]} == {str(flow.game_id), str(imported)}
    async with SessionLocal() as db:
        original = await db.get(Game, flow.game_id)
        assert original.source is None and original.external_id is None


@pytest.mark.parametrize(
    "fields",
    [
        {"release_date": date(2017, 1, 1)},
        {"platform": "PlayStation 5"},
        {"provider_ids": {"steam": "other"}},
        {"parent_game_id": "parent"},
    ],
)
async def test_known_year_platform_identity_or_variant_conflicts_are_not_suggested(flow, fields):
    async with SessionLocal() as db:
        original = await db.get(Game, flow.game_id)
        original.release_date = date(2006, 1, 1)
        original.platform = "PC"
        original.provider_ids = {"steam": "first"}
        await db.commit()
    if fields.get("parent_game_id"):
        fields = {"parent_game_id": flow.game_id}
    await add_game(flow.user_id, **fields)
    assert (await flow.client.get("/api/game-duplicates")).json()["pairs"] == []


async def test_distinct_ids_in_same_source_remain_distinct(flow):
    async with SessionLocal() as db:
        original = await db.get(Game, flow.game_id)
        original.source, original.external_id = "Steam", "1"
        await db.commit()
    await add_game(flow.user_id, source="Steam", external_id="2")
    assert (await flow.client.get("/api/game-duplicates")).json()["pairs"] == []


async def test_keep_both_is_idempotent_canonical_and_survives_plugin_replays(flow, monkeypatch):
    monkeypatch.setattr("src.features.imports.library_games.create_game_folder", lambda *_: None)
    payload = {
        "source_label": "Plugin store",
        "source_scope": "account",
        "items": [{"title": "Flow", "external_id": "42"}],
    }

    async def replay():
        async with SessionLocal() as db:
            return await dispatch_game_import(
                db, plugin_id="test.store", user_id=flow.user_id, payload=payload
            )

    imported = (await replay())["games"][0]["id"]
    bodies = [
        {"first_id": str(flow.game_id), "second_id": imported},
        {"second_id": str(flow.game_id), "first_id": imported},
    ]
    responses = await asyncio.gather(
        *(flow.client.post("/api/game-duplicates/keep-both", json=body) for body in bodies)
    )
    assert all(response.status_code == 204 for response in responses)
    assert (await replay())["games"][0]["id"] == imported
    assert (await flow.client.get("/api/game-duplicates")).json()["pairs"] == []
    async with SessionLocal() as db:
        decisions = list(
            await db.scalars(
                select(GameDuplicateDismissal).where(GameDuplicateDismissal.user_id == flow.user_id)
            )
        )
        assert len(decisions) == 1


async def test_review_excludes_trash_and_rejects_foreign_or_same_game_decisions(flow):
    foreign = User(
        id=uuid4(), username=uuid4().hex, email=f"{uuid4().hex}@example.test", password_hash="x"
    )
    async with SessionLocal() as db:
        db.add(foreign)
        await db.commit()
    try:
        foreign_game = await add_game(foreign.id)
        deleted = await add_game(flow.user_id, deleted_at=1)
        for other in (foreign_game, deleted, flow.game_id):
            response = await flow.client.post(
                "/api/game-duplicates/keep-both",
                json={"first_id": str(flow.game_id), "second_id": str(other)},
            )
            assert response.status_code == 404
        assert (await flow.client.get("/api/game-duplicates")).json()["pairs"] == []
    finally:
        async with SessionLocal() as db:
            await db.execute(delete(User).where(User.id == foreign.id))
            await db.commit()


async def test_response_is_bounded_and_queries_do_not_grow_per_game(flow):
    for _ in range(6):
        await add_game(flow.user_id)
    statements = []

    def record(_connection, _cursor, statement, _parameters, _context, _executemany):
        if statement.startswith("SELECT"):
            statements.append(statement)

    async with SessionLocal() as db:
        event.listen(db.bind.sync_engine, "before_cursor_execute", record)
        try:
            result = await suggestions(db, flow.user_id, 2)
        finally:
            event.remove(db.bind.sync_engine, "before_cursor_execute", record)
    assert len(result["pairs"]) == 2 and result["has_more"]
    assert len(statements) == 2
    assert (await flow.client.get("/api/game-duplicates?limit=101")).status_code == 422
