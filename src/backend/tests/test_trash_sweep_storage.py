"""The trash sweep finds game files under owned storage and coordinates with storage routes.

Everything runs against real temporary directories and the real test database. Pauses
hold the sweep inside its filesystem worker (with its row locks taken), or just before
it locks a row, so each interleaving with renames, uploads, restores, another sweep or
cancellation is deterministic.
"""

import asyncio
import json
import logging
import shutil
import threading
import time
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, select, update

from src.api.routes import game_archives
from src.database.models.game import Game
from src.database.models.game_archive import GameArchive, GameArchiveVersion
from src.database.models.game_file_item import GameFileItem
from src.database.models.media_item import MediaItem
from src.database.models.user import User
from src.database.session import SessionLocal
from src.features.trash import sweep
from src.helpers import save_game_asset
from tests.test_game_files_flow import _png, game_flow  # noqa: F401

KINDS = {
    "doc": SimpleNamespace(
        model=GameFileItem,
        upload="files/doc",
        path="files/doc",
        active="docs",
        file=("manual.txt", b"manual", "text/plain"),
        purge="_purge_file_item",
    ),
    "screenshot": SimpleNamespace(
        model=MediaItem,
        upload="screenshots",
        path="screenshots/screenshot",
        active="screenshots",
        file=("shot.png", _png(), "image/png"),
        purge="_purge_media_item",
    ),
}


@pytest.fixture(autouse=True)
def storage(tmp_path, monkeypatch):
    monkeypatch.setattr(save_game_asset, "DATA_ROOT", tmp_path)


@pytest.fixture(name="sweep_log")
def sweep_warnings(caplog, monkeypatch):
    """Migration tests' logging fileConfig disables existing loggers; observe the sweep anyway."""
    monkeypatch.setattr(sweep.logger, "disabled", False)
    caplog.set_level(logging.WARNING, logger=sweep.__name__)
    return caplog


@pytest.fixture(name="other")
async def other_account(flow):
    """Another account with a game using the same folder name."""
    user_id = uuid.uuid4()
    async with SessionLocal() as db:
        db.add(
            User(
                id=user_id,
                username=f"t_{user_id.hex[:10]}",
                email=f"{user_id.hex[:10]}@example.test",
                password_hash="x",
            )
        )
        await db.flush()
        game = Game(user_id=user_id, title="Flow", sort_title="Flow", folder_location="Flow")
        db.add(game)
        await db.commit()
        game_id = game.id
    yield SimpleNamespace(game_id=game_id, folder=flow.tmp / str(user_id) / "games" / "Flow")
    async with SessionLocal() as db:
        await db.execute(delete(User).where(User.id == user_id))
        await db.commit()


def expired() -> int:
    return int(time.time()) - sweep.RETENTION_SECONDS - 60


def folder(flow, name="Flow"):
    return flow.tmp / str(flow.user_id) / "games" / name


async def backdate(model, row_id) -> None:
    async with SessionLocal() as db:
        await db.execute(update(model).where(model.id == row_id).values(deleted_at=expired()))
        await db.commit()


async def stored(model, row_id):
    async with SessionLocal() as db:
        return await db.get(model, row_id)


async def upload(flow, kind="doc", file=None) -> str:
    spec = KINDS[kind]
    response = await flow.client.post(
        f"{flow.game}/{spec.upload}", files=[("files", file or spec.file)]
    )
    assert response.status_code == 200, response.text
    return response.json()["results"][0]["filename"]


async def trash(flow, kind="doc", *, expire=True) -> tuple[str, uuid.UUID]:
    spec = KINDS[kind]
    filename = await upload(flow, kind)
    response = await flow.client.delete(f"{flow.game}/{spec.path}/{filename}")
    assert response.status_code == 200, response.text
    async with SessionLocal() as db:
        item_id = await db.scalar(
            select(spec.model.id).where(
                spec.model.game_id == flow.game_id, spec.model.filename == filename
            )
        )
    if expire:
        await backdate(spec.model, item_id)
    return filename, item_id


async def other_copy(other, filename) -> uuid.UUID:
    """The other account keeps an active and a recently trashed file with the same name."""
    for subdir, data in (("_trash/doc", b"other trash"), ("docs", b"other active")):
        (other.folder / subdir).mkdir(parents=True, exist_ok=True)
        (other.folder / subdir / filename).write_bytes(data)
    async with SessionLocal() as db:
        item = GameFileItem(
            game_id=other.game_id, kind="doc", filename=filename, deleted_at=int(time.time())
        )
        db.add(item)
        await db.commit()
        return item.id


def assert_other_copy_kept(other, filename) -> None:
    assert (other.folder / "_trash" / "doc" / filename).read_bytes() == b"other trash"
    assert (other.folder / "docs" / filename).read_bytes() == b"other active"


def journal(flow, old="Flow", new="Renamed"):
    path = folder(flow).parent / ".folder-moves" / f"{flow.game_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"game_id": str(flow.game_id), "old": old, "new": new}))
    return path


class Pause:
    """Hold the sweep's worker thread for one row, after it has taken that row's locks."""

    def __init__(self, monkeypatch, name, match):
        self.entered, self.release, self.calls = threading.Event(), threading.Event(), 0
        original = getattr(sweep, name)

        def paused(*args):
            if match in args:
                self.calls += 1
                self.entered.set()
                self.release.wait(10)
            return original(*args)

        monkeypatch.setattr(sweep, name, paused)

    async def reached(self):
        assert await asyncio.to_thread(self.entered.wait, 5)


def gate(monkeypatch, name):
    """Hold the sweep after it chose its candidates but before it locks the next row."""
    reached, release = asyncio.Event(), asyncio.Event()
    original = getattr(sweep, name)

    async def gated(*args):
        reached.set()
        await release.wait()
        return await original(*args)

    monkeypatch.setattr(sweep, name, gated)
    return reached, release


async def assert_waiting(task) -> None:
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(asyncio.shield(task), 0.3)


async def test_expired_files_are_found_under_owned_storage_and_others_survive(flow, other):
    expired_doc, expired_id = await trash(flow)
    recent_doc, recent_id = await trash(flow, expire=False)
    active_doc = await upload(flow, file=("active.txt", b"active", "text/plain"))
    shot, shot_id = await trash(flow, "screenshot")
    other_id = await other_copy(other, expired_doc)

    await sweep.purge_expired_trash()

    trash_dir = folder(flow) / "_trash"
    assert not (trash_dir / "doc" / expired_doc).exists()
    assert not (trash_dir / "screenshot" / shot).exists()
    assert await stored(GameFileItem, expired_id) is None
    assert await stored(MediaItem, shot_id) is None
    assert (trash_dir / "doc" / recent_doc).is_file()
    listed = (await flow.client.get(f"{flow.game}/files/doc/trash")).json()["files"]
    assert [entry["id"] for entry in listed] == [str(recent_id)]
    downloaded = await flow.client.get(f"{flow.game}/files/doc/{active_doc}")
    assert downloaded.status_code == 200 and downloaded.content == b"active"
    assert_other_copy_kept(other, expired_doc)
    assert (await stored(GameFileItem, other_id)).deleted_at is not None


async def test_archive_trash_and_expired_versions_are_purged_in_place(flow):
    async def archive(name, data):
        response = await flow.client.post(
            f"{flow.game}/archives/save",
            data={"name": name},
            files={"file": ("save.zip", data, "application/zip")},
        )
        assert response.status_code == 200, response.text
        return response.json()

    kept = await archive("Kept", b"v1")
    for data in (b"v2-two", b"v3-three"):
        added = await flow.client.post(
            f"{flow.game}/archives/{kept['id']}/versions",
            files={"file": ("save.zip", data, "application/zip")},
        )
        assert added.status_code == 200, added.text
    versions = {v["filename"]: v for v in added.json()["versions"]}
    first = next(v for v in versions.values() if v["size"] == len(b"v1"))
    trashed = await flow.client.delete(f"{flow.game}/archives/{kept['id']}/versions/{first['id']}")
    assert trashed.status_code == 200, trashed.text
    await backdate(GameArchiveVersion, uuid.UUID(first["id"]))
    # A later, still-recoverable delete of the whole archive keeps its other versions.
    assert (await flow.client.delete(f"{flow.game}/archives/{kept['id']}")).status_code == 200
    gone = await archive("Gone", b"gone")
    assert (await flow.client.delete(f"{flow.game}/archives/{gone['id']}")).status_code == 200
    await backdate(GameArchive, uuid.UUID(gone["id"]))

    await sweep.purge_expired_trash()

    assert not (folder(flow) / "_trash" / "save" / gone["id"]).exists()
    assert await stored(GameArchive, uuid.UUID(gone["id"])) is None
    kept_files = folder(flow) / "_trash" / "save" / kept["id"] / "files"
    assert sorted(p.name for p in kept_files.iterdir()) == sorted(
        name for name in versions if name != first["filename"]
    )
    restored = await flow.client.post(f"{flow.game}/archives/{kept['id']}/restore")
    assert restored.status_code == 200, restored.text
    assert len(restored.json()["versions"]) == 2
    active = folder(flow) / "saves" / kept["id"]
    assert first["filename"] not in {p.name for p in active.iterdir()}
    for version in restored.json()["versions"]:
        assert (await flow.client.get(version["url"])).content in {b"v2-two", b"v3-three"}


async def test_deleted_game_children_and_folder_are_purged_by_game_id(flow):
    child, child_id = await trash(flow)
    assert (await flow.client.delete(f"/api/game/delete/{flow.game_id}")).status_code == 204
    game_trash = flow.tmp / "_trash" / str(flow.game_id)
    assert (game_trash / "_trash" / "doc" / child).is_file()

    await sweep.purge_expired_trash()

    assert not (game_trash / "_trash" / "doc" / child).exists() and game_trash.is_dir()
    assert await stored(GameFileItem, child_id) is None
    # Restoring the game afterwards cannot revive the expired child.
    assert (await flow.client.post(f"/api/game/{flow.game_id}/restore")).status_code == 200
    assert not (folder(flow) / "_trash" / "doc" / child).exists()

    assert (await flow.client.delete(f"/api/game/delete/{flow.game_id}")).status_code == 204
    async with SessionLocal() as db:
        replacement = Game(
            user_id=flow.user_id, title="Flow", sort_title="Flow", folder_location="Flow"
        )
        db.add(replacement)
        await db.commit()
    reused = folder(flow) / "_trash" / "doc" / child
    reused.parent.mkdir(parents=True)
    reused.write_bytes(b"replacement")
    await backdate(Game, flow.game_id)

    await sweep.purge_expired_trash()

    assert not game_trash.exists()
    assert await stored(Game, flow.game_id) is None
    assert await stored(Game, replacement.id) is not None
    assert reused.read_bytes() == b"replacement"


@pytest.mark.parametrize("link", ["owner", "folder", "trash", "kind"])
async def test_symlinked_storage_is_kept_and_never_followed(flow, other, link, sweep_log):
    filename, item_id = await trash(flow)
    await other_copy(other, filename)
    parts = {
        "owner": (folder(flow).parent.parent, other.folder.parent.parent),
        "folder": (folder(flow), other.folder),
        "trash": (folder(flow) / "_trash", other.folder / "_trash"),
        "kind": (folder(flow) / "_trash" / "doc", other.folder / "_trash" / "doc"),
    }
    source, target = parts[link]
    source.rename(flow.tmp / "moved-aside")
    try:
        source.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("Symlinks are not supported by this filesystem")

    await sweep.purge_expired_trash()

    assert_other_copy_kept(other, filename)
    assert (await stored(GameFileItem, item_id)).deleted_at is not None
    assert f"Trash sweep kept file item {item_id}" in sweep_log.text


@pytest.mark.parametrize("link", ["root", "game"])
async def test_symlinked_game_trash_is_kept(flow, link):
    folder(flow).mkdir(parents=True)
    assert (await flow.client.delete(f"/api/game/delete/{flow.game_id}")).status_code == 204
    await backdate(Game, flow.game_id)
    game_trash = flow.tmp / "_trash" / str(flow.game_id)
    (game_trash / "keep.txt").write_text("outside")
    source = game_trash.parent if link == "root" else game_trash
    outside = flow.tmp / "outside"
    source.rename(outside)
    try:
        source.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Symlinks are not supported by this filesystem")

    await sweep.purge_expired_trash()

    assert await stored(Game, flow.game_id) is not None
    assert any(path.name == "keep.txt" for path in outside.rglob("*"))


async def test_ambiguous_stored_names_are_kept(flow):
    filename, item_id = await trash(flow)
    active = await upload(flow, file=("active.txt", b"active", "text/plain"))
    async with SessionLocal() as db:
        await db.execute(
            update(GameFileItem)
            .where(GameFileItem.id == item_id)
            .values(filename=f"../../docs/{active}")
        )
        await db.commit()

    await sweep.purge_expired_trash()

    assert (folder(flow) / "docs" / active).read_bytes() == b"active"
    assert (folder(flow) / "_trash" / "doc" / filename).is_file()
    assert await stored(GameFileItem, item_id) is not None


async def test_unfinished_folder_move_waits_for_recovery(flow):
    filename, item_id = await trash(flow)
    folder(flow).rename(folder(flow, "Renamed"))
    marker = journal(flow)

    await sweep.purge_expired_trash()

    assert marker.is_file() and not folder(flow).exists()
    assert (folder(flow, "Renamed") / "_trash" / "doc" / filename).is_file()
    assert await stored(GameFileItem, item_id) is not None
    response = await flow.client.patch(
        f"/api/game/update/{flow.game_id}", json={"folder_location": "Renamed"}
    )
    assert response.status_code == 200, response.text

    await sweep.purge_expired_trash()

    assert not (folder(flow, "Renamed") / "_trash" / "doc" / filename).exists()
    assert await stored(GameFileItem, item_id) is None
    assert not marker.exists() and not folder(flow).exists()


async def test_ambiguous_folder_move_keeps_both_locations_and_its_record(flow):
    filename, item_id = await trash(flow)
    shutil.copytree(folder(flow), folder(flow, "Renamed"))
    marker = journal(flow)

    await sweep.purge_expired_trash()

    for name in ("Flow", "Renamed"):
        assert (folder(flow, name) / "_trash" / "doc" / filename).is_file()
    assert marker.is_file() and await stored(GameFileItem, item_id) is not None


async def test_expired_game_with_a_recovery_record_is_kept(flow):
    folder(flow).mkdir(parents=True)
    assert (await flow.client.delete(f"/api/game/delete/{flow.game_id}")).status_code == 204
    await backdate(Game, flow.game_id)
    marker = journal(flow)

    await sweep.purge_expired_trash()

    assert marker.is_file() and (flow.tmp / "_trash" / str(flow.game_id)).is_dir()
    assert await stored(Game, flow.game_id) is not None


async def test_rename_before_the_row_lock_is_seen_by_the_sweep(flow, monkeypatch):
    filename, item_id = await trash(flow)
    reached, release = gate(monkeypatch, "_purge_file_item")
    sweeping = asyncio.create_task(sweep.purge_expired_trash())
    try:
        await asyncio.wait_for(reached.wait(), 5)
        renamed = await flow.client.patch(
            f"/api/game/update/{flow.game_id}", json={"folder_location": "Renamed"}
        )
        assert renamed.status_code == 200, renamed.text
    finally:
        release.set()
        await sweeping
    assert not (folder(flow, "Renamed") / "_trash" / "doc" / filename).exists()
    assert not folder(flow).exists()
    assert await stored(GameFileItem, item_id) is None


async def test_rename_waits_for_a_sweep_holding_the_game(flow, monkeypatch):
    filename, item_id = await trash(flow)
    kept = await upload(flow, file=("kept.txt", b"kept", "text/plain"))
    pause = Pause(monkeypatch, "purge_media_file", filename)
    sweeping = asyncio.create_task(sweep.purge_expired_trash())
    renaming = None
    try:
        await pause.reached()
        renaming = asyncio.create_task(
            flow.client.patch(
                f"/api/game/update/{flow.game_id}", json={"folder_location": "Renamed"}
            )
        )
        await assert_waiting(renaming)
    finally:
        pause.release.set()
        await sweeping
        if renaming is not None:
            renamed = await renaming
    assert renamed.status_code == 200, renamed.text
    assert not folder(flow).exists()
    assert not (folder(flow, "Renamed") / "_trash" / "doc" / filename).exists()
    assert await stored(GameFileItem, item_id) is None
    assert (await flow.client.get(f"{flow.game}/files/doc/{kept}")).content == b"kept"


@pytest.mark.parametrize("kind", KINDS)
async def test_restore_waits_for_a_purge_in_progress(flow, monkeypatch, kind):
    spec = KINDS[kind]
    filename, item_id = await trash(flow, kind)
    pause = Pause(monkeypatch, "purge_media_file", filename)
    sweeping = asyncio.create_task(sweep.purge_expired_trash())
    restoring = None
    try:
        await pause.reached()
        restoring = asyncio.create_task(
            flow.client.post(f"{flow.game}/{spec.path}/{filename}/restore")
        )
        await assert_waiting(restoring)
    finally:
        pause.release.set()
        await sweeping
        if restoring is not None:
            restored = await restoring
    assert restored.status_code == 404, restored.text
    assert await stored(spec.model, item_id) is None
    assert not (folder(flow) / "_trash" / kind / filename).exists()
    assert not (folder(flow) / spec.active / filename).exists()


@pytest.mark.parametrize("kind", KINDS)
async def test_restore_before_the_row_lock_keeps_the_file(flow, monkeypatch, kind):
    spec = KINDS[kind]
    filename, item_id = await trash(flow, kind)
    reached, release = gate(monkeypatch, spec.purge)
    sweeping = asyncio.create_task(sweep.purge_expired_trash())
    try:
        await asyncio.wait_for(reached.wait(), 5)
        restored = await flow.client.post(f"{flow.game}/{spec.path}/{filename}/restore")
        assert restored.status_code == 200, restored.text
    finally:
        release.set()
        await sweeping
    assert (await stored(spec.model, item_id)).deleted_at is None
    downloaded = await flow.client.get(f"{flow.game}/{spec.path}/{filename}")
    assert downloaded.status_code == 200 and downloaded.content == spec.file[1]


async def test_archive_restore_waits_for_its_purge(flow, monkeypatch):
    created = await flow.client.post(
        f"{flow.game}/archives/save",
        data={"name": "Main"},
        files={"file": ("save.zip", b"Save data", "application/zip")},
    )
    archive_id = uuid.UUID(created.json()["id"])
    assert (await flow.client.delete(f"{flow.game}/archives/{archive_id}")).status_code == 200
    await backdate(GameArchive, archive_id)
    pause = Pause(monkeypatch, "purge_archive_trash", archive_id)
    sweeping = asyncio.create_task(sweep.purge_expired_trash())
    restoring = None
    try:
        await pause.reached()
        restoring = asyncio.create_task(
            flow.client.post(f"{flow.game}/archives/{archive_id}/restore")
        )
        await assert_waiting(restoring)
    finally:
        pause.release.set()
        await sweeping
        if restoring is not None:
            restored = await restoring
    assert restored.status_code == 404, restored.text
    assert await stored(GameArchive, archive_id) is None
    assert not (folder(flow) / "saves" / str(archive_id)).exists()
    assert not (folder(flow) / "_trash" / "save" / str(archive_id)).exists()


async def test_sweep_runs_beside_an_upload_and_a_queued_rename(flow, monkeypatch):
    filename, item_id = await trash(flow)
    kept = await upload(flow, file=("kept.txt", b"kept", "text/plain"))
    entered, release = asyncio.Event(), asyncio.Event()
    save = game_archives._save_upload_stream

    async def slow_upload(*args, **kwargs):
        entered.set()
        await release.wait()
        return await save(*args, **kwargs)

    monkeypatch.setattr(game_archives, "_save_upload_stream", slow_upload)
    uploading = asyncio.create_task(
        flow.client.post(
            f"{flow.game}/archives/save",
            data={"name": "Main"},
            files={"file": ("save.zip", b"Save data", "application/zip")},
        )
    )
    renaming = None
    try:
        await asyncio.wait_for(entered.wait(), 5)
        # Shared storage locks: the sweep neither waits for nor disturbs the upload.
        await asyncio.wait_for(sweep.purge_expired_trash(), 5)
        assert await stored(GameFileItem, item_id) is None
        downloaded = await flow.client.get(f"{flow.game}/files/doc/{kept}")
        assert downloaded.content == b"kept"
        renaming = asyncio.create_task(
            flow.client.patch(
                f"/api/game/update/{flow.game_id}", json={"folder_location": "Renamed"}
            )
        )
        await assert_waiting(renaming)
    finally:
        release.set()
        saved = await uploading
        if renaming is not None:
            renamed = await renaming
    assert saved.status_code == 200, saved.text
    assert renamed.status_code == 200, renamed.text
    assert not folder(flow).exists()
    assert not (folder(flow, "Renamed") / "_trash" / "doc" / filename).exists()
    version = await flow.client.get(saved.json()["versions"][0]["url"])
    assert version.status_code == 200 and version.content == b"Save data"


async def test_concurrent_sweeps_purge_a_row_once(flow, monkeypatch, sweep_log):
    filename, item_id = await trash(flow)
    pause = Pause(monkeypatch, "purge_media_file", filename)
    first = asyncio.create_task(sweep.purge_expired_trash())
    second = None
    try:
        await pause.reached()
        second = asyncio.create_task(sweep.purge_expired_trash())
        await assert_waiting(second)
    finally:
        pause.release.set()
        await first
        if second is not None:
            await second
    assert pause.calls == 1
    assert "Trash sweep kept" not in sweep_log.text
    assert await stored(GameFileItem, item_id) is None
    assert not (folder(flow) / "_trash" / "doc" / filename).exists()


async def test_cancelled_sweep_finishes_the_row_it_started(flow, monkeypatch):
    filename, item_id = await trash(flow)
    pause = Pause(monkeypatch, "purge_media_file", filename)
    sweeping = asyncio.create_task(sweep.purge_expired_trash())
    try:
        await pause.reached()
        sweeping.cancel()
        await assert_waiting(sweeping)
    finally:
        pause.release.set()
    with pytest.raises(asyncio.CancelledError):
        await sweeping
    assert await stored(GameFileItem, item_id) is None
    assert not (folder(flow) / "_trash" / "doc" / filename).exists()
    # Its row locks were released with its finished transaction.
    renamed = await asyncio.wait_for(
        flow.client.patch(f"/api/game/update/{flow.game_id}", json={"folder_location": "Renamed"}),
        5,
    )
    assert renamed.status_code == 200, renamed.text
