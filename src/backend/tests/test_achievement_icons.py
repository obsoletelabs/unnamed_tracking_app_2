"""Achievement icons are cached locally and remain scoped to their game owner."""

import io
import uuid

from PIL import Image

from src.api.routes import achievement_icons
from src.database.models.achievement import Achievement
from src.database.models.game import Game
from src.database.models.user import User
from src.database.session import SessionLocal
from src.helpers import remote_images
from tests.test_game_files_flow import game_flow  # noqa: F401


async def test_icon_cache_and_foreign_user_access(flow, monkeypatch):
    monkeypatch.setattr(achievement_icons, "_ICON_ROOT", flow.tmp)
    calls = []

    def download(url):
        calls.append(url)
        buffer = io.BytesIO()
        Image.new("RGB", (256, 256)).save(buffer, "PNG")
        return buffer.getvalue()

    monkeypatch.setattr(remote_images, "download_image", download)
    async with SessionLocal() as db:
        icon = Achievement(
            game_id=flow.game_id,
            provider="steam",
            external_id="1",
            name="Winner",
            icon_url="https://example.test/icon.png",
        )
        db.add(icon)
        await db.commit()
        icon_id = icon.id
    response = await flow.client.get(f"/api/achievement-icon/{icon_id}")
    assert response.status_code == 200
    assert Image.open(io.BytesIO(response.content)).size == (128, 128)
    again = await flow.client.get(f"/api/achievement-icon/{icon_id}")
    assert again.content == response.content
    assert len(calls) == 1
    async with SessionLocal() as db:
        stranger = User(
            id=uuid.uuid4(),
            username=f"other_{uuid.uuid4().hex}",
            email=f"{uuid.uuid4()}@example.test",
            password_hash="x",
        )
        db.add(stranger)
        await db.flush()
        game = await db.get(Game, flow.game_id)
        game.user_id = stranger.id
        await db.commit()
        stranger_id = stranger.id
    denied = await flow.client.get(f"/api/achievement-icon/{icon_id}")
    assert denied.status_code == 404
    async with SessionLocal() as db:
        await db.delete(await db.get(User, stranger_id))
        await db.commit()
