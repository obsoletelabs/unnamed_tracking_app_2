"""#256: an import says what really happened and every failed part can be retried safely."""

import uuid
from unittest.mock import AsyncMock, Mock

import pytest

from src.api.routes import library_sync
from src.database.models.game import Game
from src.database.session import SessionLocal
from src.helpers import save_game_asset
from src.helpers.remote_images import RemoteImageError
from tests.test_game_artwork_storage import picture
from tests.test_game_files_flow import game_flow  # noqa: F401
from tests.test_steam_import_steps import create_game


@pytest.fixture(autouse=True)
def storage(tmp_path, monkeypatch):
    monkeypatch.setattr(save_game_asset, "DATA_ROOT", tmp_path)


def record(title="Provider title", kinds=("key_art", "icon")):
    return {
        "title": title,
        "metadata": {"description": "from the provider"},
        "assets": [{"kind": kind, "url": f"https://art.test/{kind}.png"} for kind in kinds],
    }


async def enrich(flow, ids, **body):
    response = await flow.client.post(
        "/api/library-sync/steam/enrich", json={"game_ids": ids, **body}
    )
    assert response.status_code == 200, response.text
    return response.json()


async def test_a_game_saved_without_its_pictures_is_reported_and_can_be_retried(flow, monkeypatch):
    game_id = await create_game(flow, "Hades", app_id="620")
    broken = Mock(side_effect=RemoteImageError("provider refused"))
    monkeypatch.setattr(library_sync, "download_image", broken)
    monkeypatch.setattr(
        library_sync, "resolve_library_record", AsyncMock(return_value=(record(), []))
    )
    first = await enrich(flow, [game_id])
    # the details were read and saved, so this is not a failure, but the pictures are said to be missing
    assert first["enriched"] == 1 and first["failed"] == 0
    assert first["artwork_missing"] == 1 and first["artwork_missing_game_ids"] == [game_id]
    async with SessionLocal() as db:
        game = await db.get(Game, uuid.UUID(game_id))
        assert game.description == "from the provider"
        game.description = "I rewrote this"  # a person edits the game before retrying
        await db.commit()

    monkeypatch.setattr(library_sync, "download_image", Mock(return_value=picture()))
    retry = await enrich(flow, [game_id], artwork_only=True)
    assert retry["artwork_missing"] == 0 and retry["artwork_missing_game_ids"] == []
    async with SessionLocal() as db:
        game = await db.get(Game, uuid.UUID(game_id))
        assert game.description == "I rewrote this"  # retry never rewrites details
    folder = flow.tmp / str(flow.user_id) / "games" / "Hades"
    assert (folder / "key_art.png").is_file() and (folder / "icon.png").is_file()

    # asking again is idempotent: nothing is fetched for pictures the game already has
    fetched = Mock(return_value=picture())
    monkeypatch.setattr(library_sync, "download_image", fetched)
    again = await enrich(flow, [game_id], artwork_only=True)
    assert again["artwork_missing"] == 0
    fetched.assert_not_called()


async def test_a_provider_that_offers_no_pictures_is_reported_differently_from_a_failed_download(
    flow, monkeypatch
):
    game_id = await create_game(flow, "Hades", app_id="620")
    monkeypatch.setattr(
        library_sync, "resolve_library_record", AsyncMock(return_value=(record(kinds=()), []))
    )
    result = await enrich(flow, [game_id])
    assert result["enriched"] == 1 and result["artwork_missing_game_ids"] == [game_id]


async def test_failed_games_are_named_and_the_rest_are_kept(flow, monkeypatch):
    good = await create_game(flow, "Good", app_id="10")
    bad = await create_game(flow, "Bad", app_id="20")

    async def resolve(*_args, **_kwargs):
        raise TimeoutError("provider timeout")

    monkeypatch.setattr(library_sync, "download_image", Mock(return_value=picture()))
    monkeypatch.setattr(
        library_sync, "resolve_library_record", AsyncMock(return_value=(record(), []))
    )
    first = await enrich(flow, [good])
    assert first["failed_game_ids"] == []
    monkeypatch.setattr(library_sync, "resolve_library_record", resolve)
    second = await enrich(flow, [bad])
    assert second["failed"] == 1 and second["failed_game_ids"] == [bad] and second["enriched"] == 0
    monkeypatch.setattr(
        library_sync, "resolve_library_record", AsyncMock(return_value=(record(), []))
    )
    retried = await enrich(flow, [bad])
    assert retried["enriched"] == 1 and retried["failed_game_ids"] == []
