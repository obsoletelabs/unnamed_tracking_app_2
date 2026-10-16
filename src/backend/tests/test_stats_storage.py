"""Storage used on the stats dashboard counts only the caller's own game folders."""

import uuid
from pathlib import Path

import pytest
from sqlalchemy import delete

from src.api.routes.stats import get_stats_overview
from src.database.models.game import Game
from src.database.models.user import User
from src.database.session import SessionLocal
from src.helpers import save_game_asset
from src.main import app  # noqa: F401 — register the complete application model graph


@pytest.fixture(name="accounts")
async def scratch_accounts(tmp_path, monkeypatch):
    monkeypatch.setattr(save_game_asset, "DATA_ROOT", tmp_path)
    ids = [uuid.uuid4(), uuid.uuid4()]
    async with SessionLocal() as db:
        db.add_all(
            User(
                id=user_id,
                username=f"stats-{user_id.hex[:12]}",
                email=f"{user_id.hex[:12]}@example.test",
                password_hash="unused",
            )
            for user_id in ids
        )
        await db.commit()
    yield ids
    async with SessionLocal() as db:
        await db.execute(delete(User).where(User.id.in_(ids)))
        await db.commit()


async def _add_games(*games: tuple[uuid.UUID, str]) -> None:
    async with SessionLocal() as db:
        db.add_all(
            Game(user_id=user_id, title=folder, sort_title=folder, folder_location=folder)
            for user_id, folder in games
        )
        await db.commit()


async def _storage_used_bytes(user_id: uuid.UUID) -> int:
    async with SessionLocal() as db:
        user = await db.get(User, user_id)
        assert user is not None
        return (await get_stats_overview(db=db, current_user=user))["storage_used_bytes"]


def _write(path: Path, size: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)


async def test_storage_counts_owned_files_and_not_another_accounts_same_named_folder(
    accounts, tmp_path
):
    owner, other = accounts
    await _add_games((owner, "Shared"), (other, "Shared"))
    owned = tmp_path / str(owner) / "games" / "Shared"
    _write(owned / "cover.png", 3)
    _write(owned / "notes" / "Guide.md", 100)
    _write(owned / "screenshots" / "shot.png", 20)
    _write(owned / "world_map" / "tiles" / "0.png", 5_000)  # legacy render cache, not counted
    _write(tmp_path / str(other) / "games" / "Shared" / "saves" / "big.zip", 7_000)

    assert await _storage_used_bytes(owner) == 123
    assert await _storage_used_bytes(other) == 7_000


async def test_storage_skips_a_symlinked_game_folder(accounts, tmp_path):
    owner, _ = accounts
    await _add_games((owner, "Linked"))
    outside = tmp_path / "outside"
    _write(outside / "elsewhere.bin", 9_000)
    games_root = tmp_path / str(owner) / "games"
    games_root.mkdir(parents=True)
    try:
        (games_root / "Linked").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Symlinks are not supported by this filesystem")

    assert await _storage_used_bytes(owner) == 0
