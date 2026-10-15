"""Artwork saves retain storage locks and can use uncommitted imported games."""

import asyncio
import io
import threading
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from PIL import Image
from sqlalchemy import select, text

from src.api.routes import library_sync
from src.database.models.game import Game
from src.database.models.user import User
from src.database.session import SessionLocal
from src.helpers import save_game_asset
from tests.test_game_files_flow import game_flow  # noqa: F401


def picture():
    buffer = io.BytesIO()
    Image.new("RGB", (4, 4), "orange").save(buffer, "PNG")
    return buffer.getvalue()


@pytest.fixture(autouse=True)
def storage(tmp_path, monkeypatch):
    monkeypatch.setattr(save_game_asset, "DATA_ROOT", tmp_path)


@pytest.mark.parametrize("cancellations", [0, 1, 2])
async def test_artwork_keeps_the_row_lock_until_previews_finish(flow, monkeypatch, cancellations):
    entered, release = threading.Event(), threading.Event()

    def preview(*_args):
        entered.set()
        if not release.wait(5):
            raise TimeoutError("Preview test was not released")

    monkeypatch.setattr(save_game_asset, "make_standard_previews", preview)
    application_name = f"artwork-lock-{uuid4().hex}"

    async def change_folder():
        async with SessionLocal() as db:
            await db.execute(
                text("SELECT set_config('application_name', :name, true)"),
                {"name": application_name},
            )
            game = await db.scalar(select(Game).where(Game.id == flow.game_id).with_for_update())
            game.folder_location = "Changed"
            await db.commit()

    saving = asyncio.create_task(save_game_asset.save_game_asset(picture(), flow.game_id, "icon"))
    changing = None
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        for _ in range(cancellations):
            saving.cancel()
            await asyncio.sleep(0)
        changing = asyncio.create_task(change_folder())
        blocked = False
        for _ in range(100):
            async with SessionLocal() as db:
                blocked = bool(
                    await db.scalar(
                        text(
                            "SELECT EXISTS (SELECT 1 FROM pg_stat_activity "
                            "WHERE application_name = :name AND wait_event_type = 'Lock')"
                        ),
                        {"name": application_name},
                    )
                )
            if blocked or changing.done():
                break
            await asyncio.sleep(0.01)
        assert blocked, "Folder update must wait for the asset and its previews"
    finally:
        release.set()
        if cancellations:
            with pytest.raises(asyncio.CancelledError):
                await saving
            output = flow.tmp / str(flow.user_id) / "games" / "Flow" / "icon.png"
        else:
            output = await saving
        if changing is not None:
            await changing
    assert output == flow.tmp / str(flow.user_id) / "games" / "Flow" / "icon.png"
    assert output.is_file()


@pytest.mark.parametrize("fresh", [False, True])
async def test_import_artwork_uses_the_callers_locked_game_without_a_second_row_lookup(
    flow, monkeypatch, fresh
):
    monkeypatch.setattr(library_sync, "download_image", Mock(return_value=picture()))
    monkeypatch.setattr(
        library_sync,
        "resolve_library_record",
        AsyncMock(
            return_value=({"assets": [{"kind": "icon", "url": "https://art.test/icon"}]}, [])
        ),
    )
    async with SessionLocal() as db:
        if fresh:
            game = Game(
                user_id=flow.user_id, title="Fresh", sort_title="fresh", folder_location="Fresh"
            )
            db.add(game)
            await db.flush()
        else:
            game = await db.scalar(select(Game).where(Game.id == flow.game_id).with_for_update())
        user = await db.get(User, flow.user_id)
        await asyncio.wait_for(library_sync._enrich_new_game(game, user, {}), 2)
        output = flow.tmp / str(flow.user_id) / "games" / game.folder_location / "icon.png"
        assert output.is_file()
        with Image.open(output) as saved:
            assert saved.size == save_game_asset.ASSET_SIZES["icon"]
        await db.commit()
