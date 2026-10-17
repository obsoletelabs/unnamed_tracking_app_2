"""Existing import routes still work and now record scoped observations (#94)."""

from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, func, select

from src.api.routes import games, library_sync
from src.api.schemas.game import GameUpdate
from src.database.models.game import Game, GameStatus
from src.database.models.game_identity import (
    GameSourceConnection,
    GameSourceObservation,
    GameSourceObservationLink,
    GameSourceObservationRevision,
)
from src.database.models.game_identity_tracking import GameFieldValue
from src.database.models.user import User
from src.database.session import SessionLocal
from src.features.imports import steam_connection
from src.helpers import save_game_asset
from src.plugin_api.game_import import dispatch_game_import
from tests.test_game_files_flow import game_flow  # noqa: F401
from tests.test_playnite_contract import clients, storage  # noqa: F401
from tests.test_synced_game_progression import owned_snapshot, provider_game


@pytest.fixture(autouse=True)
def isolated_import_storage(tmp_path, monkeypatch):
    monkeypatch.setattr(save_game_asset, "DATA_ROOT", tmp_path)


async def observations(user_id, source=None):
    async with SessionLocal() as db:
        statement = (
            select(GameSourceObservation, GameSourceConnection)
            .join(
                GameSourceConnection, GameSourceConnection.id == GameSourceObservation.connection_id
            )
            .where(GameSourceObservation.user_id == user_id)
            .order_by(GameSourceObservation.external_id)
        )
        if source:
            statement = statement.where(GameSourceConnection.source == source)
        return (await db.execute(statement)).all()


async def link_targets(observation_id):
    async with SessionLocal() as db:
        return list(
            await db.scalars(
                select(GameSourceObservationLink.game_id).where(
                    GameSourceObservationLink.observation_id == observation_id,
                    GameSourceObservationLink.unlinked_at.is_(None),
                )
            )
        )


async def revision_count(user_id):
    async with SessionLocal() as db:
        return await db.scalar(
            select(func.count())
            .select_from(GameSourceObservationRevision)
            .where(GameSourceObservationRevision.user_id == user_id)
        )


# --- Playnite ---------------------------------------------------------------------------------


async def test_playnite_create_retry_and_rename_update_one_observation(clients):  # noqa: F811
    playnite = await clients()
    guid = str(uuid4())
    body = {"title": "Hades", "folder_location": "Hades", "playnite_guid": guid}
    created = await playnite.api.post("/api/game/create", json=body)
    assert created.status_code == 201
    game_id = UUID(created.json()["id"])
    ((row, connection),) = await observations(playnite.user_id)
    assert (connection.source, connection.source_scope, row.external_id) == (
        "playnite",
        "legacy",
        guid,
    )
    assert await link_targets(row.id) == [game_id] and row.revision == 1

    retry = await playnite.api.post("/api/game/create", json=body)
    assert retry.status_code == 200
    assert await revision_count(playnite.user_id) == 1

    renamed = await playnite.api.post("/api/game/create", json={**body, "title": "Hades (2020)"})
    assert renamed.status_code == 200 and renamed.json()["id"] == str(game_id)
    ((row, _),) = await observations(playnite.user_id)
    assert (row.observed_title, row.revision) == ("Hades (2020)", 2)


async def test_playnite_update_records_only_the_fields_it_sent(clients):  # noqa: F811
    playnite = await clients()
    guid = str(uuid4())
    created = await playnite.api.post(
        "/api/game/create",
        json={
            "title": "Hades",
            "folder_location": "Hades",
            "playnite_guid": guid,
            "platform": "PC",
        },
    )
    game_id = created.json()["id"]
    update_response = await playnite.api.patch(
        f"/api/game/update/{game_id}", json={"playtime_seconds": 90, "favorite": True}
    )
    assert update_response.status_code == 200
    ((row, _),) = await observations(playnite.user_id)
    assert row.observed_title == "Hades" and row.observed_platform == "PC"
    assert row.observed_facts["playtime_seconds"] == 90 and row.observed_facts["favorite"] is True
    assert "description" not in row.observed_facts
    again = await playnite.api.patch(f"/api/game/update/{game_id}", json={"playtime_seconds": 90})
    assert again.status_code == 200
    ((row, _),) = await observations(playnite.user_id)
    assert row.revision == 2


async def test_the_same_playnite_guid_for_two_users_never_collides(clients):  # noqa: F811
    first, second = await clients(), await clients()
    guid = str(uuid4())
    for index, user in enumerate((first, second)):
        response = await user.api.post(
            "/api/game/create",
            json={"title": f"Hades {index}", "folder_location": "Hades", "playnite_guid": guid},
        )
        assert response.status_code == 201
    one, two = await observations(first.user_id), await observations(second.user_id)
    assert one[0][0].id != two[0][0].id
    assert (one[0][0].observed_title, two[0][0].observed_title) == ("Hades 0", "Hades 1")


async def test_a_playnite_game_in_trash_is_reported_not_recreated(clients):  # noqa: F811
    playnite = await clients()
    guid = str(uuid4())
    body = {"title": "Hades", "folder_location": "Hades", "playnite_guid": guid}
    game_id = (await playnite.api.post("/api/game/create", json=body)).json()["id"]
    assert (await playnite.api.delete(f"/api/game/delete/{game_id}")).status_code == 204
    again = await playnite.api.post("/api/game/create", json=body)
    assert again.status_code == 409
    assert again.json()["detail"]["error"] == "playnite_game_in_trash"
    assert again.json()["detail"]["game_id"] == game_id
    async with SessionLocal() as db:
        count = await db.scalar(
            select(func.count()).select_from(Game).where(Game.user_id == playnite.user_id)
        )
    assert count == 1
    assert (await playnite.api.post(f"/api/game/{game_id}/restore")).status_code == 200
    assert (await playnite.api.post("/api/game/create", json=body)).status_code == 200


# --- manual entries -----------------------------------------------------------------------------


async def test_manual_entries_are_recorded_under_their_own_id_and_edits_update_them(flow):  # noqa: F811
    ((row, connection),) = await observations(flow.user_id, "manual")
    assert (connection.source_scope, row.external_id) == ("local", str(flow.game_id))
    assert await link_targets(row.id) == [flow.game_id]
    async with SessionLocal() as db:
        await games.update_game(
            flow.game_id,
            GameUpdate(playtime_seconds=300),
            db,
            type("U", (), {"id": flow.user_id})(),
        )
    ((row, _),) = await observations(flow.user_id, "manual")
    assert row.observed_facts["playtime_seconds"] == 300 and row.observed_title == "Flow"


async def test_a_user_edit_of_an_imported_game_is_not_source_evidence(flow, monkeypatch):  # noqa: F811
    await provider_game(flow, "Steam", GameStatus.BACKLOG)
    async with SessionLocal() as db:
        # name provenance (#98) refers to the observation, which the database protects
        await db.execute(delete(GameFieldValue).where(GameFieldValue.user_id == flow.user_id))
        await db.execute(
            delete(GameSourceObservation).where(GameSourceObservation.user_id == flow.user_id)
        )
        await db.commit()
        await games.update_game(
            flow.game_id, GameUpdate(playtime_seconds=7), db, type("U", (), {"id": flow.user_id})()
        )
    assert await observations(flow.user_id) == []


# --- Steam / RetroAchievements / PlayStation -----------------------------------------------------


@pytest.mark.parametrize(
    ("source", "key", "handler"),
    [
        ("Steam", "steam", library_sync.sync_steam_library),
        ("RetroAchievements", "retroachievements", library_sync.sync_retroachievements_library),
        ("PlayStation", "playstation", library_sync.sync_psn_library),
    ],
)
async def test_host_syncs_complete_and_a_repeat_adds_nothing(
    flow, monkeypatch, source, key, handler
):  # noqa: F811
    await provider_game(flow, source, GameStatus.BACKLOG)
    owned_snapshot(monkeypatch, source, unlocked=1, total=2)
    for _ in range(2):
        async with SessionLocal() as db:
            await handler(db, await db.get(User, flow.user_id))
    ((row, connection),) = await observations(flow.user_id, key)
    assert (connection.source_scope, row.external_id, row.observed_title) == (
        "legacy",
        "10",
        "Flow",
    )
    assert row.observed_facts["owned"] is True
    assert row.observed_facts["achievements_total"] == 2
    assert row.observed_facts["achievements_unlocked"] == 1
    assert row.revision == 1 and await link_targets(row.id) == [flow.game_id]


async def test_steam_playtime_change_updates_the_observation_and_keeps_the_rest(flow, monkeypatch):  # noqa: F811
    await provider_game(flow, "Steam", GameStatus.BACKLOG)
    owned_snapshot(monkeypatch, "Steam", playtime=10)
    async with SessionLocal() as db:
        await library_sync.sync_steam_library(db, await db.get(User, flow.user_id))
    owned_snapshot(monkeypatch, "Steam", playtime=20, total=0)
    async with SessionLocal() as db:
        await library_sync.sync_steam_library(db, await db.get(User, flow.user_id))
    ((row, _),) = await observations(flow.user_id, "steam")
    assert row.observed_facts["playtime_seconds"] == 1200 and row.revision == 2
    # the second payload reported no achievements, so the first one's counts survive
    assert row.observed_facts["achievements_total"] == 1


async def test_a_steam_response_that_omits_a_game_never_removes_it(flow, monkeypatch):  # noqa: F811
    await provider_game(flow, "Steam", GameStatus.BACKLOG)
    owned_snapshot(monkeypatch, "Steam")
    async with SessionLocal() as db:
        await library_sync.sync_steam_library(db, await db.get(User, flow.user_id))
    monkeypatch.setattr(
        library_sync.steam, "get_owned_games", lambda *_: [{"appid": 11, "name": "Other"}]
    )
    async with SessionLocal() as db:
        await library_sync.sync_steam_library(db, await db.get(User, flow.user_id))
    rows = {row.external_id: row for row, _ in await observations(flow.user_id, "steam")}
    assert set(rows) == {"10", "11"} and rows["10"].availability == "available"


async def test_playstation_complete_pull_marks_missing_titles_unavailable(flow, monkeypatch):  # noqa: F811
    await provider_game(flow, "PlayStation", GameStatus.BACKLOG)
    owned_snapshot(monkeypatch, "PlayStation")
    async with SessionLocal() as db:
        await library_sync.sync_psn_library(db, await db.get(User, flow.user_id))
    monkeypatch.setattr(
        library_sync.PSNClient,
        "get_owned_titles",
        lambda *_: [{"npCommunicationId": "11", "trophyTitleName": "Other"}],
    )
    async with SessionLocal() as db:
        await library_sync.sync_psn_library(db, await db.get(User, flow.user_id))
    rows = {row.external_id: row for row, _ in await observations(flow.user_id, "playstation")}
    assert rows["10"].availability == "unavailable" and rows["10"].observed_title == "Flow"
    assert rows["11"].availability == "available"


async def test_steam_disconnect_keeps_observations_and_a_resync_reconnects(flow, monkeypatch):  # noqa: F811
    await provider_game(flow, "Steam", GameStatus.BACKLOG)
    owned_snapshot(monkeypatch, "Steam")
    async with SessionLocal() as db:
        await library_sync.sync_steam_library(db, await db.get(User, flow.user_id))
    async with SessionLocal() as db:
        await steam_connection.disconnect(db, flow.user_id)
    ((row, connection),) = await observations(flow.user_id, "steam")
    assert connection.disconnected_at is not None and row.availability == "available"
    async with SessionLocal() as db:
        user = await db.get(User, flow.user_id)
        user.steam_id, user.steam_api_key = "76561197960287930", "key"
        await db.commit()
        await library_sync.sync_steam_library(db, user)
    ((again, connection),) = await observations(flow.user_id, "steam")
    assert connection.disconnected_at is None and again.id == row.id


async def test_steam_wishlist_records_the_id_but_not_the_placeholder_title(flow, monkeypatch):  # noqa: F811
    from src.api.routes import steam_import_steps

    monkeypatch.setattr(
        steam_import_steps, "load_preferences", lambda *_: _async({"steam_import_wishlist": True})
    )
    monkeypatch.setattr(steam_import_steps.steam_wishlist, "get_wishlist_app_ids", lambda *_: [77])
    monkeypatch.setattr(
        steam_import_steps.steam_connection,
        "credentials",
        lambda *_: _async(("76561197960287930", "key")),
    )
    monkeypatch.setattr(steam_import_steps.steam_connection, "recheck", lambda *_: _async(None))
    async with SessionLocal() as db:
        result = await steam_import_steps.import_wishlist(db, await db.get(User, flow.user_id))
    assert result["added"] == 1
    ((row, _),) = await observations(flow.user_id, "steam")
    assert row.external_id == "77" and row.observed_title is None
    assert row.observed_facts == {"wishlisted": True}


async def _async(value):
    return value


# --- plugins ------------------------------------------------------------------------------------


def plugin_payload(scope="acct-1", **item):
    return {
        "source_label": "Example store",
        "source_scope": scope,
        "items": [{"external_id": "ns:item", "title": "Selected game", **item}],
    }


async def plugin_import(user_id, data, plugin_id="test.import"):
    async with SessionLocal() as db:
        return await dispatch_game_import(db, plugin_id=plugin_id, user_id=user_id, payload=data)


async def test_plugin_imports_are_scoped_and_idempotent(flow):  # noqa: F811
    first = (await plugin_import(flow.user_id, plugin_payload(playtime_seconds=60)))["games"][0]
    await plugin_import(flow.user_id, plugin_payload(playtime_seconds=60))
    await plugin_import(flow.user_id, plugin_payload(scope="acct-2"))
    rows = await observations(flow.user_id, "test.import")
    assert sorted(connection.source_scope for _, connection in rows) == ["acct-1", "acct-2"]
    one = next(row for row, connection in rows if connection.source_scope == "acct-1")
    assert one.revision == 1 and one.observed_facts == {"playtime_seconds": 60}
    assert await link_targets(one.id) == [UUID(first["id"])]
    renamed = plugin_payload(title="Renamed", playtime_seconds=90)
    await plugin_import(flow.user_id, renamed)
    rows = await observations(flow.user_id, "test.import")
    one = next(row for row, connection in rows if connection.source_scope == "acct-1")
    assert (one.observed_title, one.revision) == ("Renamed", 2)


async def test_plugin_unavailable_report_is_recorded_without_deleting(flow):  # noqa: F811
    await plugin_import(flow.user_id, plugin_payload())
    await plugin_import(flow.user_id, plugin_payload(available=False))
    await plugin_import(flow.user_id, plugin_payload(available=False))
    ((row, _),) = await observations(flow.user_id, "test.import")
    assert (row.availability, row.observed_title, row.revision) == (
        "unavailable",
        "Selected game",
        2,
    )
