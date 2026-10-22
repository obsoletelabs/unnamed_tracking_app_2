"""Provider achievement catalogues: contract, row mapping, storage and the route fallbacks."""

import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Response
from pydantic import ValidationError
from sqlalchemy import delete, select

from src.api.routes import games, library_sync
from src.api.schemas.game import GameCreate
from src.database.models.achievement import Achievement
from src.database.models.game import Game
from src.database.models.user import User
from src.database.session import SessionLocal
from src.features import achievement_catalogue
from src.features.achievement_catalogue import catalogue_rows, store_catalogue
from src.plugin_api.metadata_contracts import (
    MetadataAchievement,
    MetadataPatch,
    MetadataProviderRegistration,
    MetadataProviderRequest,
)

ITEM = {"name": "Wake Up Call", "external_id": "ACH_1", "global_percent": 40.5}


def test_contract_accepts_catalogues_and_the_achievements_resource():
    patch = MetadataPatch(
        achievements=[ITEM, {"name": "Secret", "external_id": "s", "hidden": True}]
    )
    assert patch.achievements[0].global_percent == 40.5 and patch.achievements[1].hidden is True
    request = MetadataProviderRequest(
        request_id=uuid.uuid4(), user_id=uuid.uuid4(), query="x", resource="achievements"
    )
    assert request.resource == "achievements"
    registration = MetadataProviderRegistration.model_validate(
        {
            "provider_id": "p",
            "name": "P",
            "media_types": ["game"],
            "metadata_resources": ["entity", "achievements"],
            "operations": {"metadata": "metadata", "health": "health"},
        }
    )
    assert "achievements" in registration.metadata_resources


@pytest.mark.parametrize(
    "bad",
    [
        {"name": "", "external_id": "x"},
        {"name": "n"},
        {"name": "n", "external_id": "x", "global_percent": 101},
        {"name": "n", "external_id": "x", "icon_url": "javascript:alert(1)"},
        {"name": "n", "external_id": "x", "unlocked": True},
        {"name": "x" * 201, "external_id": "x"},
    ],
)
def test_contract_rejects_bad_achievement_definitions(bad):
    with pytest.raises(ValidationError):
        MetadataAchievement.model_validate(bad)


def test_rows_are_never_unlocked_and_deduplicated_by_external_id():
    record = {
        "provider_name": "Atlas",
        "metadata": {
            "achievements": [
                {**ITEM, "hidden": False, "description": "d", "icon_url": None},
                {"name": "Again", "external_id": "ACH_1"},
                {"name": "Other", "external_id": "ACH_2", "hidden": True},
            ]
        },
    }
    rows = catalogue_rows(record)
    assert [r["external_id"] for r in rows] == ["ACH_1", "ACH_2"]
    assert all(r["unlocked"] is False for r in rows) and rows[1]["hidden"] is True
    assert achievement_catalogue.provider_label(record) == "Atlas"
    assert achievement_catalogue.provider_label({}) == "Metadata providers"
    assert catalogue_rows({}) == [] and catalogue_rows({"metadata": {"achievements": None}}) == []


def test_provider_label_uses_the_contributor_for_a_library_candidate():
    provider = SimpleNamespace(id="official.metadata-atlas.atlas", name="Atlas")
    record = {"provider_name": "Library", "contributors": [provider.id]}
    assert achievement_catalogue.provider_label(record, [provider]) == "Atlas"


@pytest.fixture
async def scratch_game(monkeypatch):
    monkeypatch.setattr(games, "create_game_folder", lambda *_args: None)
    async with SessionLocal() as db:
        user = User(
            username=f"t_{uuid.uuid4().hex[:10]}",
            email=f"{uuid.uuid4().hex[:10]}@example.test",
            password_hash="x",
        )
        db.add(user)
        await db.commit()
        user_id = user.id
        created = await games.create_game(
            GameCreate(title="Portal 2", folder_location="Portal_2"),
            Response(),
            db,
            SimpleNamespace(id=user_id),
        )
        game_id = created.id
    yield SimpleNamespace(user_id=user_id, game_id=game_id)
    async with SessionLocal() as db:
        await db.execute(delete(User).where(User.id == user_id))
        await db.commit()


async def _rows(game_id):
    async with SessionLocal() as db:
        return list(
            (await db.scalars(select(Achievement).where(Achievement.game_id == game_id))).all()
        )


async def test_store_upserts_keeps_links_and_removes_dropped_definitions(scratch_game):
    first = [
        {**ITEM, "description": "d", "icon_url": None, "hidden": False, "unlocked": False},
        {
            "external_id": "ACH_2",
            "name": "Two",
            "description": None,
            "icon_url": None,
            "hidden": False,
            "global_percent": None,
            "unlocked": False,
        },
    ]
    async with SessionLocal() as db:
        await store_catalogue(db, scratch_game.game_id, "Atlas", first)
        await db.commit()
    kept = {r.external_id: r.id for r in await _rows(scratch_game.game_id)}
    second = [{**first[0], "name": "Renamed", "global_percent": 12.0, "description": None}]
    async with SessionLocal() as db:
        await store_catalogue(db, scratch_game.game_id, "Atlas", second)
        await db.commit()
    rows = await _rows(scratch_game.game_id)
    assert [r.external_id for r in rows] == ["ACH_1"] and rows[0].id == kept["ACH_1"]  # same row
    assert rows[0].name == "Renamed" and rows[0].global_percent == 12.0
    assert rows[0].description == "d"  # a missing description never erases a known one
    assert rows[0].unlocked is False


async def test_route_fetches_a_catalogue_for_games_without_a_synced_provider(
    scratch_game, monkeypatch
):
    record = {"provider_name": "Atlas", "metadata": {"achievements": [ITEM]}}

    async def collect(_db, _user, _candidate, **options):
        assert options["resource"] == "achievements"
        return record, []

    monkeypatch.setattr(achievement_catalogue, "collect_record", collect)
    async with SessionLocal() as db:
        user = await db.get(User, scratch_game.user_id)
        result = await library_sync.refresh_game_achievements(scratch_game.game_id, db, user)
    assert result == {"provider": "Atlas", "achievements": 1, "unlocked": 0, "hidden": 0}
    stored = await _rows(scratch_game.game_id)
    assert [(r.provider, r.source_provider) for r in stored] == [("catalog", "Atlas")]


async def test_route_imports_atlas_catalogue_alongside_steam_progress(scratch_game, monkeypatch):
    atlas_id = "atlas:01m5h9c0bz0gk20mw9ks9y6cw8"
    progress = {
        "external_id": "ACH_1",
        "name": "Wake Up Call",
        "description": None,
        "icon_url": None,
        "unlocked": True,
        "unlocked_at": 1_700_000_000,
        "hidden": False,
    }
    catalogue = catalogue_rows(
        {
            "metadata": {
                "achievements": [
                    {
                        **ITEM,
                        "description": "Wake up in the relaxation vault.",
                        "source_ids": {"steam": "ACH_1"},
                    },
                    {"name": "Local Integration Achievement", "external_id": "atlas-2"},
                ]
            }
        }
    )

    async def steam_rows(_user, external_id, _db):
        assert external_id == "620"
        return [progress]

    async def fetch_atlas(_db, _user_id, game):
        assert game.provider_ids["atlas"] == atlas_id
        return "Atlas", catalogue, []

    monkeypatch.setattr(library_sync, "_fetch_steam_rows", steam_rows)
    monkeypatch.setattr(library_sync, "fetch_catalogue", fetch_atlas)
    async with SessionLocal() as db:
        game = await db.get(Game, scratch_game.game_id)
        game.source = "Steam"
        game.external_id = "620"
        game.provider_ids = {"steam": "620", "atlas": atlas_id}
        user = await db.get(User, scratch_game.user_id)
        await db.commit()
        result = await library_sync.refresh_game_achievements(game.id, db, user)
        listed = await games.list_game_achievements(game.id, db, user)

    assert result == {
        "provider": "Steam + Atlas",
        "achievements": 2,
        "unlocked": 1,
        "hidden": 0,
    }
    by_name = {item["name"]: item for item in listed}
    assert set(by_name) == {"Wake Up Call", "Local Integration Achievement"}
    assert by_name["Wake Up Call"]["unlocked"] is True
    assert by_name["Wake Up Call"]["description"] == "Wake up in the relaxation vault."
    assert by_name["Local Integration Achievement"]["unlocked"] is False
    assert by_name["Local Integration Achievement"]["provider"] == "Atlas"


async def test_catalogue_import_succeeds_when_platform_progress_is_unavailable(
    scratch_game, monkeypatch
):
    async def steam_failure(*_args):
        raise HTTPException(status_code=502, detail="Steam is unavailable")

    async def fetch_atlas(*_args):
        return "Atlas", catalogue_rows({"metadata": {"achievements": [ITEM]}}), []

    monkeypatch.setattr(library_sync, "_fetch_steam_rows", steam_failure)
    monkeypatch.setattr(library_sync, "fetch_catalogue", fetch_atlas)
    async with SessionLocal() as db:
        game = await db.get(Game, scratch_game.game_id)
        game.source = "Steam"
        game.external_id = "620"
        user = await db.get(User, scratch_game.user_id)
        await db.commit()
        result = await library_sync.refresh_game_achievements(game.id, db, user)

    assert result["provider"] == "Atlas"
    assert result["achievements"] == 1 and result["unlocked"] == 0
    assert result["warnings"] == ["Steam progress was unavailable: Steam is unavailable"]
    assert [(row.provider, row.source_provider) for row in await _rows(scratch_game.game_id)] == [
        ("catalog", "Atlas")
    ]


async def test_route_returns_zero_when_no_provider_offers_achievements(scratch_game, monkeypatch):
    async def collect(*_args, **_options):
        return {"metadata": {}}, []

    monkeypatch.setattr(achievement_catalogue, "collect_record", collect)
    async with SessionLocal() as db:
        user = await db.get(User, scratch_game.user_id)
        result = await library_sync.refresh_game_achievements(scratch_game.game_id, db, user)
    assert result == {
        "provider": "No new data",
        "achievements": 0,
        "unlocked": 0,
        "hidden": 0,
    }


async def test_route_surfaces_catalogue_errors_when_no_data_is_available(scratch_game, monkeypatch):
    async def failed_lookup(*_args):
        return "Atlas", [], ["Atlas is unavailable"]

    monkeypatch.setattr(library_sync, "fetch_catalogue", failed_lookup)
    async with SessionLocal() as db:
        user = await db.get(User, scratch_game.user_id)
        with pytest.raises(HTTPException) as raised:
            await library_sync.refresh_game_achievements(scratch_game.game_id, db, user)

    assert raised.value.status_code == 502
    assert raised.value.detail == "Metadata provider lookup failed: Atlas is unavailable"
