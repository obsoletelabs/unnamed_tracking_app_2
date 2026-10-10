"""Ownership grouping keeps original game data and provider replay identities."""

import asyncio
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete

from src.database.models.game import Game, GameStatus
from src.database.models.user import User
from src.database.session import SessionLocal
from src.features.imports.library_games import get_or_create_game
from src.plugin_api.game_import import dispatch_game_import
from tests.test_game_files_flow import game_flow  # noqa: F401


async def add_copy(flow, **fields):
    async with SessionLocal() as db:
        copy = Game(
            user_id=flow.user_id,
            title="Flow",
            sort_title="flow",
            folder_location=uuid4().hex,
            **fields,
        )
        db.add(copy)
        await db.commit()
        return copy.id


async def group(flow, main_id, copy_id):
    return await flow.client.post(
        "/api/game-ownership/group", json={"main_id": str(main_id), "copy_id": str(copy_id)}
    )


async def separate(flow, main_id, copy_id):
    return await flow.client.post(
        "/api/game-ownership/separate", json={"main_id": str(main_id), "copy_id": str(copy_id)}
    )


@pytest.mark.parametrize("source", ["Steam", "Epic Games", "GOG", "Plugin store"])
async def test_group_and_separate_preserve_both_entries_and_files(flow, source):
    copy_id = await add_copy(
        flow,
        source=source,
        external_id="remote-42",
        platform="PC",
        notes="Copy notes",
        playtime_seconds=12345,
        status=GameStatus.MASTERED,
        locked_fields=["title", "playtime_seconds"],
        provider_ids={"store": "42"},
    )
    file_response = await flow.client.post(
        f"/api/game/{copy_id}/files/doc",
        files={"files": ("copy.txt", b"Copy document", "text/plain")},
    )
    assert file_response.status_code == 200, file_response.text
    before = (await flow.client.get(f"/api/game/get/{copy_id}")).json()
    main_before = (await flow.client.get(f"/api/game/get/{flow.game_id}")).json()
    files_before = (await flow.client.get(f"/api/game/{copy_id}/files/doc")).json()
    assert (await group(flow, flow.game_id, copy_id)).status_code == 204
    assert (await group(flow, flow.game_id, copy_id)).status_code == 204
    after = (await flow.client.get(f"/api/game/get/{copy_id}")).json()
    for key, value in before.items():
        if key not in {"parent_game_id", "relationship_type", "updated_at"}:
            assert after[key] == value, key
    assert after["parent_game_id"] == str(flow.game_id)
    assert after["relationship_type"] == "owned_copy"
    assert (await flow.client.get(f"/api/game/get/{flow.game_id}")).json() == main_before
    assert (await flow.client.get(f"/api/game/{copy_id}/files/doc")).json() == files_before
    assert (await flow.client.get("/api/game-duplicates")).json()["pairs"] == []
    assert (await separate(flow, flow.game_id, copy_id)).status_code == 204
    assert (await separate(flow, flow.game_id, copy_id)).status_code == 204
    restored = (await flow.client.get(f"/api/game/get/{copy_id}")).json()
    assert restored["parent_game_id"] is None and restored["relationship_type"] is None
    assert restored["notes"] == "Copy notes"
    assert (await flow.client.get(f"/api/game/{copy_id}/files/doc")).json() == files_before


async def test_plugin_replay_updates_same_copy_and_preserves_main_tracking(flow, monkeypatch):
    monkeypatch.setattr("src.features.imports.library_games.create_game_folder", lambda *_: None)
    payload = {
        "source_label": "Plugin store",
        "source_scope": "account",
        "items": [{"title": "Flow", "external_id": "42", "playtime_seconds": 60}],
    }

    async def replay():
        async with SessionLocal() as db:
            return await dispatch_game_import(
                db, plugin_id="test.store", user_id=flow.user_id, payload=payload
            )

    copy_id = UUID((await replay())["games"][0]["id"])
    async with SessionLocal() as db:
        main = await db.get(Game, flow.game_id)
        main.notes, main.status = "Shared plan", GameStatus.PLAYING
        copy = await db.get(Game, copy_id)
        copy.notes, copy.status = "Store-specific plan", GameStatus.MASTERED
        await db.commit()
    assert (await group(flow, flow.game_id, copy_id)).status_code == 204
    payload["items"][0]["playtime_seconds"] = 120
    result = (await replay())["games"][0]
    assert result["id"] == str(copy_id) and not result["created"]
    assert result["playtime_seconds"] == 120 and result["status"] == "MASTERED"
    async with SessionLocal() as db:
        main, copy = await db.get(Game, flow.game_id), await db.get(Game, copy_id)
        assert (main.notes, main.status) == ("Shared plan", GameStatus.PLAYING)
        assert copy.parent_game_id == main.id and copy.notes == "Store-specific plan"
    assert (await separate(flow, flow.game_id, copy_id)).status_code == 204
    assert (await replay())["games"][0]["id"] == str(copy_id)


async def test_steam_identity_lookup_retains_grouped_copy(flow, monkeypatch):
    monkeypatch.setattr("src.features.imports.library_games.create_game_folder", lambda *_: None)
    async with SessionLocal() as db:
        copy, created = await get_or_create_game(db, flow.user_id, "Flow", "Steam", "620")
        assert created
        copy_id = copy.id
        await db.commit()
    assert (await group(flow, flow.game_id, copy_id)).status_code == 204
    async with SessionLocal() as db:
        copy, created = await get_or_create_game(db, flow.user_id, "Flow", "Steam", "620")
        assert not created and copy.id == copy_id and copy.parent_game_id == flow.game_id


async def test_concurrent_reverse_groups_cannot_make_a_cycle(flow):
    copy_id = await add_copy(flow)
    responses = await asyncio.gather(
        group(flow, flow.game_id, copy_id), group(flow, copy_id, flow.game_id)
    )
    assert sorted(response.status_code for response in responses) == [204, 409]


async def test_nested_groups_variants_and_stale_separation_are_rejected(flow):
    copy_id, other_id = await add_copy(flow), await add_copy(flow)
    assert (await group(flow, flow.game_id, copy_id)).status_code == 204
    assert (await group(flow, copy_id, other_id)).status_code == 409
    assert (await group(flow, other_id, flow.game_id)).status_code == 409
    assert (await separate(flow, other_id, copy_id)).status_code == 409
    variant_id = await add_copy(flow, parent_game_id=flow.game_id, relationship_type="dlc")
    assert (await group(flow, flow.game_id, variant_id)).status_code == 409
    assert (await group(flow, variant_id, other_id)).status_code == 409


async def test_normal_relationship_api_cannot_bypass_group_invariants(flow):
    copy_id, other_id = await add_copy(flow), await add_copy(flow)
    assert (await group(flow, flow.game_id, copy_id)).status_code == 204
    response = await flow.client.patch(
        f"/api/game/update/{flow.game_id}",
        json={"parent_game_id": str(other_id), "relationship_type": "owned_copy"},
    )
    assert response.status_code == 400
    response = await flow.client.post(
        "/api/game/create",
        json={
            "title": "Another copy",
            "folder_location": uuid4().hex,
            "parent_game_id": str(copy_id),
            "relationship_type": "owned_copy",
        },
    )
    assert response.status_code == 400


async def test_deleted_main_leaves_copy_accessible_and_restore_keeps_group(flow):
    copy_id = await add_copy(flow)
    assert (await group(flow, flow.game_id, copy_id)).status_code == 204
    assert (await flow.client.delete(f"/api/game/delete/{flow.game_id}")).status_code == 204
    assert (await flow.client.get(f"/api/game/get/{copy_id}")).status_code == 200
    assert (await flow.client.post(f"/api/game/{flow.game_id}/restore")).status_code == 200
    copy = (await flow.client.get(f"/api/game/get/{copy_id}")).json()
    assert copy["parent_game_id"] == str(flow.game_id)


async def test_foreign_trashed_same_id_and_malformed_requests_are_rejected(flow):
    async with SessionLocal() as db:
        stranger = User(
            username=uuid4().hex, email=f"{uuid4().hex}@test.invalid", password_hash="x"
        )
        db.add(stranger)
        await db.flush()
        foreign = Game(
            user_id=stranger.id, title="Flow", sort_title="flow", folder_location="foreign"
        )
        db.add(foreign)
        await db.commit()
        stranger_id, foreign_id = stranger.id, foreign.id
    try:
        trashed_id = await add_copy(flow, deleted_at=1)
        for copy_id in (foreign_id, trashed_id, flow.game_id, uuid4()):
            assert (await group(flow, flow.game_id, copy_id)).status_code == 404
        assert (await separate(flow, flow.game_id, foreign_id)).status_code == 404
        assert (
            await flow.client.post(
                "/api/game-ownership/group",
                json={
                    "main_id": str(flow.game_id),
                    "copy_id": str(uuid4()),
                    "automatic": True,
                },
            )
        ).status_code == 422
    finally:
        async with SessionLocal() as db:
            await db.execute(delete(User).where(User.id == stranger_id))
            await db.commit()
