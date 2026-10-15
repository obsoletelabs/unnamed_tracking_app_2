"""Community completion estimates round-trip without replacing personal playtime."""

from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from src.api.routes import export_import, game_metadata, library_sync
from src.database.models.game import Game
from src.database.models.user import User
from src.database.session import SessionLocal
from src.plugin_api.metadata_contracts import MetadataPatch
from tests.test_game_files_flow import game_flow  # noqa: F401

TIMES = {
    "time_to_beat_hours": 10.5,
    "time_to_beat_extras_hours": 16.2,
    "time_to_beat_completionist_hours": 25.8,
    "time_to_beat_all_hours": 18.1,
}


def test_optional_metadata_times_preserve_main_only_plugins():
    legacy = MetadataPatch(time_to_beat_hours=10.5)
    assert legacy.time_to_beat_hours == 10.5
    assert legacy.model_dump().get("time_to_beat_extras_hours") is None
    assert MetadataPatch(**TIMES).model_dump(include=set(TIMES)) == TIMES


@pytest.mark.parametrize("value", [-1, float("inf"), float("nan")])
def test_provider_time_estimates_must_be_finite_and_nonnegative(value):
    with pytest.raises(ValidationError) as caught:
        MetadataPatch(time_to_beat_completionist_hours=value)
    assert caught.value.errors()[0]["type"] in {"greater_than_equal", "finite_number"}


async def test_times_survive_edit_export_and_restore_without_changing_playtime(flow, monkeypatch):
    monkeypatch.setattr(export_import, "create_game_folder", lambda *_args: None)
    edited = await flow.client.patch(
        f"/api/game/update/{flow.game_id}", json={**TIMES, "playtime_seconds": 1234}
    )
    assert edited.status_code == 200, edited.text
    for name, value in TIMES.items():
        assert Decimal(str(edited.json()[name])) == Decimal(str(value))
        assert name in edited.json()["locked_fields"]
    snapshot = (await flow.client.get("/api/export/library")).json()
    saved = next(game for game in snapshot["games"] if game["id"] == str(flow.game_id))
    imported = await flow.client.post("/api/import/library", json=[saved])
    assert imported.status_code == 200 and imported.json()["created"] == 1, imported.text
    games = (await flow.client.get("/api/game/list")).json()
    restored = next(game for game in games if game["id"] != str(flow.game_id))
    assert {name: restored[name] for name in TIMES} == {name: saved[name] for name in TIMES}
    cleared = await flow.client.patch(
        f"/api/game/update/{flow.game_id}", json={"time_to_beat_extras_hours": None}
    )
    assert cleared.json()["time_to_beat_extras_hours"] is None
    assert cleared.json()["time_to_beat_hours"] == saved["time_to_beat_hours"]
    assert cleared.json()["playtime_seconds"] == restored["playtime_seconds"] == 1234


async def test_refresh_fills_available_categories_but_preserves_manual_estimates(flow, monkeypatch):
    edited = await flow.client.patch(
        f"/api/game/update/{flow.game_id}",
        json={"time_to_beat_completionist_hours": 42, "playtime_seconds": 1234},
    )
    assert edited.status_code == 200
    monkeypatch.setattr(
        game_metadata,
        "resolve_library_record",
        AsyncMock(return_value=({"title": "Flow", "metadata": {"title": "Flow", **TIMES}}, [])),
    )
    refreshed = await flow.client.post(
        f"{flow.game}/metadata/refresh", json={"fill_missing_art": False}
    )
    assert refreshed.status_code == 200, refreshed.text
    assert "time_to_beat_completionist_hours" in refreshed.json()["skipped_locked_fields"]
    game = (await flow.client.get(f"/api/game/get/{flow.game_id}")).json()
    assert Decimal(str(game["time_to_beat_completionist_hours"])) == 42
    assert Decimal(str(game["time_to_beat_extras_hours"])) == Decimal("16.2")
    assert Decimal(str(game["time_to_beat_all_hours"])) == Decimal("18.1")
    assert game["playtime_seconds"] == 1234


@pytest.mark.parametrize("enabled", [False, True])
async def test_library_enrichment_obeys_the_time_estimates_preference(flow, monkeypatch, enabled):
    monkeypatch.setattr(
        library_sync,
        "resolve_library_record",
        AsyncMock(return_value=({"title": "Flow", "metadata": {"title": "Flow", **TIMES}}, [])),
    )
    async with SessionLocal() as db:
        game = await db.scalar(select(Game).where(Game.id == flow.game_id).with_for_update())
        user = await db.get(User, flow.user_id)
        await library_sync._enrich_new_game(game, user, {"save_time_to_beat": enabled})
        for name, value in TIMES.items():
            assert getattr(game, name) == (value if enabled else None)
