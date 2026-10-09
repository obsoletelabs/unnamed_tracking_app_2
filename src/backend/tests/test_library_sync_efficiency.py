"""Bulk imports keep provider identity and library lookup costs stable."""

from unittest.mock import Mock
from uuid import uuid4

import pytest
from sqlalchemy import event

from src.api.routes import library_sync
from src.database.models.game import FOLDER_NAME_MAX_LENGTH, Game
from src.database.models.user import User
from src.database.session import SessionLocal
from src.features.imports import library_games
from src.helpers import save_game_asset
from tests.test_game_files_flow import game_flow  # noqa: F401


@pytest.fixture(autouse=True)
def isolated_import_storage(tmp_path, monkeypatch):
    monkeypatch.setattr(save_game_asset, "_DATA_ROOT", tmp_path)


async def test_steam_sync_uses_constant_library_selects(flow, monkeypatch):
    entries = [{"appid": number, "name": f"Game {number}"} for number in range(1, 9)]
    monkeypatch.setattr(library_sync.steam, "resolve_steam_id", Mock(return_value="123"))
    monkeypatch.setattr(library_sync.steam, "get_owned_games", Mock(return_value=entries))
    monkeypatch.setattr(library_sync.steam, "get_schema_for_game", Mock(return_value={}))
    monkeypatch.setattr(library_sync.steam, "get_player_achievements", Mock(return_value=[]))
    monkeypatch.setattr(library_games, "create_game_folder", lambda *_: None)
    statements = []

    def record_statement(_connection, _cursor, statement, _parameters, _context, _executemany):
        if statement.startswith("SELECT") and "FROM games" in statement:
            statements.append(statement)

    async with SessionLocal() as db:
        user = await db.get(User, flow.user_id)
        user.steam_id, user.steam_api_key = "123", "key"
        for entry in entries[:5]:
            db.add(
                Game(
                    user_id=user.id,
                    title=entry["name"],
                    sort_title=entry["name"].lower(),
                    source="Steam",
                    external_id=str(entry["appid"]),
                    folder_location=uuid4().hex,
                )
            )
        await db.commit()
        event.listen(db.bind.sync_engine, "before_cursor_execute", record_statement)
        try:
            result = await library_sync.sync_steam_library(db, user)
        finally:
            event.remove(db.bind.sync_engine, "before_cursor_execute", record_statement)
        assert (result["games_added"], result["games_updated"]) == (3, 5)
        assert len(statements) <= 3


@pytest.mark.parametrize("cached", [False, True])
async def test_same_title_with_distinct_provider_ids_creates_distinct_games(flow, cached):
    async with SessionLocal() as db:
        db.add(
            Game(
                user_id=flow.user_id,
                title="Prey",
                sort_title="prey",
                source="Steam",
                external_id="2006",
                folder_location="Prey",
            )
        )
        await db.commit()
        index = await library_games.LibraryIndex.load(db, flow.user_id, "Steam") if cached else None
        found, created = await library_sync._get_or_create_game(
            db, flow.user_id, "Prey", "Steam", "2017", index=index
        )
        assert created
        assert found.external_id == "2017"
        await db.commit()


@pytest.mark.parametrize("collision", [False, True])
async def test_import_folder_names_fit_the_column(flow, collision):
    title = "a" * FOLDER_NAME_MAX_LENGTH
    async with SessionLocal() as db:
        if collision:
            db.add(
                Game(
                    user_id=flow.user_id,
                    title="Existing",
                    sort_title="existing",
                    folder_location=title,
                )
            )
            await db.commit()
        game, created = await library_sync._get_or_create_game(
            db, flow.user_id, title + " extra", "Steam", "10"
        )
        assert created
        assert len(game.folder_location) <= FOLDER_NAME_MAX_LENGTH
        assert game.folder_location == (title[:-2] + "-2" if collision else title)
        await db.commit()


async def test_cached_matches_preserve_locks_and_remember_adopted_provider_id(flow):
    async with SessionLocal() as db:
        original = Game(
            user_id=flow.user_id,
            title="My title",
            sort_title="my sort",
            source="Steam",
            folder_location="My-title",
            locked_fields=["title"],
        )
        db.add(original)
        await db.commit()
        index = await library_games.LibraryIndex.load(db, flow.user_id, "Steam")
        adopted, created = await library_sync._get_or_create_game(
            db, flow.user_id, "My title", "Steam", "10", index=index
        )
        assert not created and adopted.id == original.id
        found, created = await library_sync._get_or_create_game(
            db, flow.user_id, "Provider title", "Steam", "10", index=index
        )
        assert not created and found.id == original.id
        assert (found.title, found.sort_title) == ("My title", "my sort")
        assert index.find("My title", None) is found
        assert index.find("Provider title", None) is None
        with pytest.raises(ValueError, match="scope mismatch"):
            await library_sync._get_or_create_game(db, uuid4(), "My title", "Steam", index=index)
        await db.commit()


async def test_other_users_folder_names_do_not_change_import_folder(flow):
    async with SessionLocal() as db:
        other = User(
            username=f"folders_{uuid4().hex}",
            email=f"{uuid4()}@example.test",
            password_hash="x",
        )
        db.add(other)
        await db.flush()
        db.add(
            Game(user_id=other.id, title="Shared", sort_title="shared", folder_location="Shared")
        )
        await db.commit()
        try:
            game, created = await library_sync._get_or_create_game(
                db, flow.user_id, "Shared", "Steam", "10"
            )
            assert created and game.folder_location == "Shared"
            await db.commit()
        finally:
            await db.rollback()
            await db.delete(other)
            await db.commit()
