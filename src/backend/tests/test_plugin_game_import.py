"""Public game imports preserve identity, local edits and gateway authorization."""

import asyncio
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from src.database.models.game import Game, GameStatus
from src.database.session import SessionLocal
from src.helpers import save_game_asset
from src.plugin_api import gateway
from src.plugin_api.game_import import dispatch_game_import, game_import_identity
from tests.test_game_files_flow import game_flow  # noqa: F401


@pytest.fixture(autouse=True)
def isolated_import_storage(tmp_path, monkeypatch):
    monkeypatch.setattr(save_game_asset, "DATA_ROOT", tmp_path)


def payload(*, scope="account:library", **item):
    return {
        "source_label": "Example store",
        "source_scope": scope,
        "items": [{"external_id": "namespace:item", "title": "Selected game", **item}],
    }


async def import_games(user_id, data, plugin_id="test.import"):
    async with SessionLocal() as db:
        return await dispatch_game_import(db, plugin_id=plugin_id, user_id=user_id, payload=data)


async def test_retry_identity_survives_title_label_and_playtime_changes(flow, tmp_path):
    first = (await import_games(flow.user_id, payload(playtime_seconds=600)))["games"][0]
    assert first["created"] and first["status"] == "PLAYED"
    renamed = payload(title="Renamed game", playtime_seconds=0)
    renamed["source_label"] = "Renamed store"
    second = (await import_games(flow.user_id, renamed))["games"][0]
    assert second["id"] == first["id"] and not second["created"]
    assert second["title"] == "Renamed game" and second["playtime_seconds"] == 600
    async with SessionLocal() as db:
        game = await db.get(Game, UUID(first["id"]))
        assert game.source == "Example store"
        assert (tmp_path / str(flow.user_id) / "games" / game.folder_location / "notes").is_dir()


async def test_title_match_never_adopts_manual_or_other_provider_game(flow):
    first = (await import_games(flow.user_id, payload(title="Flow")))["games"][0]
    second = (await import_games(flow.user_id, payload(title="Flow"), "other.import"))["games"][0]
    other_scope = (await import_games(flow.user_id, payload(scope="other-account", title="Flow")))[
        "games"
    ][0]
    assert len({first["id"], second["id"], other_scope["id"], str(flow.game_id)}) == 4
    async with SessionLocal() as db:
        original = await db.get(Game, flow.game_id)
        assert original.external_id is None and original.source is None


def test_identity_is_actor_plugin_source_and_external_scoped():
    user = uuid4()
    first = game_import_identity("test.import", user, "account:library", "namespace:item")
    assert first == game_import_identity("test.import", user, "account:library", "namespace:item")
    assert (
        len(
            {
                first,
                game_import_identity("other.import", user, "account:library", "namespace:item"),
                game_import_identity("test.import", uuid4(), "account:library", "namespace:item"),
                game_import_identity("test.import", user, "other-account", "namespace:item"),
                game_import_identity(
                    "test.import", user, "account:library", "other-namespace:item"
                ),
            }
        )
        == 5
    )


async def test_import_preserves_manual_status_locks_notes_and_provider_ids(flow):
    first = (await import_games(flow.user_id, payload(provider_ids={"store": "original"})))[
        "games"
    ][0]
    async with SessionLocal() as db:
        game = await db.get(Game, UUID(first["id"]))
        game.status, game.notes, game.rating_overall = GameStatus.MASTERED, "My notes", 9
        game.title, game.sort_title, game.description = "My title", "my sort", "My description"
        game.locked_fields = ["title", "description"]
        await db.commit()
    updated = (
        await import_games(
            flow.user_id,
            payload(
                title="Provider title",
                description="Provider description",
                developer="Developer",
                provider_ids={"new-provider": "another"},
                playtime_seconds=1200,
            ),
        )
    )["games"][0]
    assert updated["title"] == "My title" and updated["status"] == "MASTERED"
    async with SessionLocal() as db:
        game = await db.get(Game, UUID(first["id"]))
        assert (game.sort_title, game.description, game.developer) == (
            "my sort",
            "My description",
            "Developer",
        )
        assert game.notes == "My notes" and game.rating_overall == 9
        assert game.provider_ids == {"store": "original", "new-provider": "another"}


async def test_unavailable_and_unknown_playtime_do_not_reset_game_state(flow):
    first = (await import_games(flow.user_id, payload(playtime_seconds=600, description="Known")))[
        "games"
    ][0]
    absent = (await import_games(flow.user_id, payload(available=False, playtime_seconds=0)))[
        "games"
    ][0]
    assert not absent["available"] and absent["playtime_seconds"] == 600
    async with SessionLocal() as db:
        game = await db.get(Game, UUID(first["id"]))
        first_stale = game.stale_since
        assert first_stale is not None and game.description == "Known"
    await import_games(flow.user_id, payload(available=False))
    async with SessionLocal() as db:
        assert (await db.get(Game, UUID(first["id"]))).stale_since == first_stale
    returned = (await import_games(flow.user_id, payload()))["games"][0]
    assert returned["available"] and returned["playtime_seconds"] == 600
    assert (
        await import_games(flow.user_id, payload(external_id="never-imported", available=False))
    )["games"][0]["skipped"] == "not_imported"


@pytest.mark.parametrize("conflict", ["deleted", "identity_changed"])
async def test_import_never_restores_trash_or_overwrites_changed_identity(flow, conflict):
    first = (await import_games(flow.user_id, payload()))["games"][0]
    async with SessionLocal() as db:
        game = await db.get(Game, UUID(first["id"]))
        if conflict == "deleted":
            game.deleted_at = 123
        else:
            game.external_id = "changed-by-user"
        await db.commit()
    result = (await import_games(flow.user_id, payload(title="Changed", playtime_seconds=100)))[
        "games"
    ][0]
    assert result["conflict"] == conflict and not result["created"]
    async with SessionLocal() as db:
        game = await db.get(Game, UUID(first["id"]))
        assert game.title == "Selected game" and game.playtime_seconds == 0
        assert game.deleted_at == (123 if conflict == "deleted" else None)


async def test_concurrent_retries_create_one_game_and_preserve_maximum_playtime(flow):
    results = await asyncio.gather(
        import_games(flow.user_id, payload(playtime_seconds=600)),
        import_games(flow.user_id, payload(playtime_seconds=1200)),
    )
    assert len({result["games"][0]["id"] for result in results}) == 1
    assert sum(result["games"][0]["created"] for result in results) == 1
    async with SessionLocal() as db:
        games = list(
            await db.scalars(
                select(Game).where(
                    Game.user_id == flow.user_id, Game.external_id == "namespace:item"
                )
            )
        )
        assert len(games) == 1 and games[0].playtime_seconds == 1200


async def test_oversized_or_invalid_batches_are_rejected_before_any_database_work():
    invalid = [
        {**payload(), "items": []},
        {
            **payload(),
            "items": [{"external_id": str(index), "title": "Game"} for index in range(26)],
        },
        {**payload(), "items": payload()["items"] * 2},
        {**payload(), "user_id": str(uuid4())},
        {**payload(), "source_scope": " "},
        payload(title=" "),
        payload(title="x" * 501),
        payload(external_id=""),
        payload(playtime_seconds=-1),
        payload(playtime_seconds=True),
        payload(playtime_seconds=2_000_000_001),
        payload(status="MASTERED"),
        payload(game_id=str(uuid4())),
        payload(locked_fields=[]),
        payload(provider_ids={"../provider": "id"}),
        payload(available="false"),
        payload(tags=["x"] * 51),
        payload(description="x" * 20001),
    ]
    for data in invalid:
        with pytest.raises(ValidationError):
            await dispatch_game_import(
                object(), plugin_id="test.import", user_id=uuid4(), payload=data
            )


async def test_gateway_requires_live_write_grant_before_import(flow, monkeypatch):
    installation_id = uuid4()
    denied = AsyncMock(return_value=False)
    monkeypatch.setattr(gateway, "has_capability_grant", denied)
    async with SessionLocal() as db:
        arguments = dict(
            plugin_id="test.import",
            installation_id=installation_id,
            user_id=flow.user_id,
            method="games.import",
            payload=payload(),
        )
        with pytest.raises(PermissionError, match="requires capability games.write"):
            await gateway.dispatch_gateway_request(db, **arguments, capability="games.read")
        denied.assert_not_awaited()
        with pytest.raises(PermissionError, match="has not been granted"):
            await gateway.dispatch_gateway_request(db, **arguments, capability="games.write")
        assert denied.await_args.kwargs["user_id"] == flow.user_id
        assert denied.await_args.kwargs["installation_id"] == installation_id
        monkeypatch.setattr(gateway, "has_capability_grant", AsyncMock(return_value=True))
        result = await gateway.dispatch_gateway_request(db, **arguments, capability="games.write")
        assert result["games"][0]["created"]
        monkeypatch.setattr(gateway, "has_capability_grant", denied)
        with pytest.raises(PermissionError, match="has not been granted"):
            await gateway.dispatch_gateway_request(db, **arguments, capability="games.write")
