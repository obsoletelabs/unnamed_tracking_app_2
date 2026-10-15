"""Folder edits preserve storage and can recover an interrupted database/filesystem save."""

import asyncio
import json
from pathlib import Path

import pytest
from sqlalchemy import select

from src.api.routes import game_archives
from src.database.models.game import Game
from src.database.session import SessionLocal
from src.features import game_folders
from src.helpers import save_game_asset
from tests.test_game_files_flow import game_flow  # noqa: F401


@pytest.fixture(autouse=True)
def storage(tmp_path, monkeypatch):
    monkeypatch.setattr(save_game_asset, "DATA_ROOT", tmp_path)


def folder(flow, name="Flow"):
    return flow.tmp / str(flow.user_id) / "games" / name


def journal(flow):
    path = folder(flow).parent / ".folder-moves" / f"{flow.game_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"game_id": str(flow.game_id), "old": "Flow", "new": "Renamed"}),
        encoding="utf-8",
    )
    return path


async def test_folder_edit_moves_every_file_and_existing_urls_keep_working(flow):
    contents = {
        "notes/Guide.md": b"Keep this note",
        "docs/manual.txt": b"Original manual",
        "screenshots/image.png": b"Original screenshot",
        "saves/profile/version.zip": b"Original save",
        ".trash/docs/old.txt": b"Recoverable document",
    }
    for name, data in contents.items():
        path = folder(flow) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    response = await flow.client.patch(
        f"/api/game/update/{flow.game_id}", json={"folder_location": "Renamed"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["folder_location"] == "Renamed"
    assert not folder(flow).exists()
    assert {
        p.relative_to(folder(flow, "Renamed")).as_posix(): p.read_bytes()
        for p in folder(flow, "Renamed").rglob("*")
        if p.is_file()
    } == contents
    note = await flow.client.get(f"/api/game/{flow.game_id}/notes/Guide")
    assert note.status_code == 200 and note.text == "Keep this note"
    manual = await flow.client.get(f"/api/game/{flow.game_id}/files/doc/manual.txt")
    assert manual.status_code == 200 and manual.content == b"Original manual"


async def test_existing_destination_is_never_overwritten(flow):
    folder(flow).mkdir(parents=True)
    folder(flow, "Renamed").mkdir()
    response = await flow.client.patch(
        f"/api/game/update/{flow.game_id}", json={"folder_location": "Renamed"}
    )
    assert response.status_code == 409, response.text
    assert folder(flow).is_dir() and folder(flow, "Renamed").is_dir()
    async with SessionLocal() as db:
        assert (await db.get(Game, flow.game_id)).folder_location == "Flow"


@pytest.mark.parametrize("committed", [False, True])
async def test_database_failure_reconciles_using_the_persisted_name(flow, monkeypatch, committed):
    folder(flow).mkdir(parents=True)
    (folder(flow) / "keep.txt").write_text("keep")
    async with SessionLocal() as db:
        game = await db.scalar(select(Game).where(Game.id == flow.game_id).with_for_update())
        game.folder_location = "Renamed"
        commit = db.commit

        async def fail_commit():
            if committed:
                await commit()
            raise OSError("Commit acknowledgement lost")

        monkeypatch.setattr(db, "commit", fail_commit)
        with pytest.raises(OSError, match="acknowledgement"):
            await game_folders.commit_folder_move(db, game, "Flow", flow.tmp)
        current = await db.get(Game, flow.game_id)
        expected = "Renamed" if committed else "Flow"
        assert current.folder_location == expected
        assert (folder(flow, expected) / "keep.txt").read_text() == "keep"
        assert not folder(flow, "Flow" if committed else "Renamed").exists()
        assert not list((folder(flow).parent / ".folder-moves").glob("*.json"))


async def test_failed_rename_rolls_back_the_database(flow, monkeypatch):
    folder(flow).mkdir(parents=True)
    rename = Path.rename

    def fail_rename(path, destination):
        if path == folder(flow):
            raise OSError("Read-only storage")
        return rename(path, destination)

    monkeypatch.setattr(Path, "rename", fail_rename)
    async with SessionLocal() as db:
        game = await db.scalar(select(Game).where(Game.id == flow.game_id).with_for_update())
        game.folder_location = "Renamed"
        with pytest.raises(OSError, match="Read-only"):
            await game_folders.commit_folder_move(db, game, "Flow", flow.tmp)
        assert game.folder_location == "Flow"
        assert folder(flow).is_dir() and not folder(flow, "Renamed").exists()


async def test_interrupted_move_blocks_writes_and_retries_safely(flow):
    folder(flow, "Renamed").mkdir(parents=True)
    (folder(flow, "Renamed") / "keep.txt").write_text("keep")
    marker = journal(flow)
    blocked = await flow.client.post(
        f"/api/game/{flow.game_id}/notes/New", json={"content": "must wait"}
    )
    assert blocked.status_code == 409, blocked.text
    with pytest.raises(game_folders.FolderStorageError, match="unfinished"):
        save_game_asset.create_game_folder(flow.user_id, "Renamed")
    assert not folder(flow).exists()
    response = await flow.client.patch(
        f"/api/game/update/{flow.game_id}", json={"folder_location": "Renamed"}
    )
    assert response.status_code == 200, response.text
    assert (folder(flow, "Renamed") / "keep.txt").read_text() == "keep"
    assert not marker.exists() and not folder(flow).exists()


async def test_ambiguous_recovery_preserves_both_folders(flow):
    folder(flow).mkdir(parents=True)
    folder(flow, "Renamed").mkdir()
    marker = journal(flow)
    response = await flow.client.patch(
        f"/api/game/update/{flow.game_id}", json={"folder_location": "Renamed"}
    )
    assert response.status_code == 409, response.text
    assert marker.is_file() and folder(flow).is_dir() and folder(flow, "Renamed").is_dir()


def test_symlinked_storage_is_rejected(tmp_path):
    owner = "00000000-0000-0000-0000-000000000001"
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (tmp_path / owner).symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Symlinks are not supported by this filesystem")
    with pytest.raises(game_folders.FolderStorageError, match="symlink"):
        game_folders.owned_folder(tmp_path, owner, "Game")


async def test_folder_move_waits_for_an_archive_upload_and_keeps_its_download(flow, monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()
    save = game_archives._save_upload_stream

    async def slow_upload(*args, **kwargs):
        entered.set()
        await release.wait()
        return await save(*args, **kwargs)

    monkeypatch.setattr(game_archives, "_save_upload_stream", slow_upload)
    upload = asyncio.create_task(
        flow.client.post(
            f"{flow.game}/archives/save",
            data={"name": "Main save"},
            files={"file": ("save.zip", b"Save data", "application/zip")},
        )
    )
    moving = None
    try:
        await asyncio.wait_for(entered.wait(), 2)
        moving = asyncio.create_task(
            flow.client.patch(
                f"/api/game/update/{flow.game_id}", json={"folder_location": "Renamed"}
            )
        )
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(asyncio.shield(moving), 0.1)
    finally:
        release.set()
        saved = await upload
        if moving is not None:
            moved = await moving
    assert saved.status_code == 200, saved.text
    assert moved.status_code == 200, moved.text
    assert not folder(flow).exists()
    downloaded = await flow.client.get(saved.json()["versions"][0]["url"])
    assert downloaded.status_code == 200 and downloaded.content == b"Save data"
