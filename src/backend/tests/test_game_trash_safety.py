"""Whole-game trash and restore never delete existing data (issue #461).

The first half drives the filesystem helpers against real temporary directories: collisions of
every kind, repeated delete/import cycles, and moves interrupted at every step. The second half
runs the delete/restore routes and the sweep against the real test database and checks both the
stored rows and the bytes on disk after injected commit and move failures.
"""

import asyncio
import errno
import logging
import os
import shutil
import uuid
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models.game import Game
from src.database.session import SessionLocal
from src.features import game_folders
from src.features.trash import game_trash, sweep
from src.helpers import save_game_asset
from tests.test_game_files_flow import game_flow  # noqa: F401  # pylint: disable=unused-import

GAME_ID = uuid.UUID("11111111-2222-3333-4444-555555555555")


def write(root: Path, relative: str, data: bytes) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def all_bytes(root: Path) -> list[bytes]:
    return sorted(path.read_bytes() for path in root.rglob("*") if path.is_file())


def tree(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


@pytest.fixture(name="dirs")
def directories(tmp_path):
    active = tmp_path / "user" / "games" / "Flow"
    return active, game_trash.game_trash_dir(tmp_path, GAME_ID), tmp_path


# --------------------------------------------------------------- filesystem helpers


def test_move_into_empty_trash_is_a_plain_move(dirs):
    active, trash, root = dirs
    write(active, "notes/a.md", b"a")
    game_trash.move_game_to_trash(active, root, GAME_ID)
    assert tree(trash) == {"notes/a.md": b"a"}
    assert not active.exists()


def test_move_without_an_active_folder_is_a_no_op(dirs):
    active, trash, root = dirs
    game_trash.move_game_to_trash(active, root, GAME_ID)
    assert not trash.exists()


def test_existing_trash_destination_keeps_both_copies(dirs):
    active, trash, root = dirs
    write(trash, "shot.png", b"old shot")
    write(trash, "only-old.txt", b"only old")
    write(active, "shot.png", b"new shot")
    write(active, "only-new.txt", b"only new")

    game_trash.move_game_to_trash(active, root, GAME_ID)

    assert not active.exists()
    assert (trash / "shot.png").read_bytes() == b"new shot"
    assert (trash / "shot (preserved-1).png").read_bytes() == b"old shot"
    assert (trash / "only-old.txt").read_bytes() == b"only old"
    assert (trash / "only-new.txt").read_bytes() == b"only new"


def test_existing_active_destination_keeps_both_copies_on_restore(dirs):
    active, trash, root = dirs
    write(trash, "saves/slot1.sav", b"trashed save")
    write(trash, "keep.txt", b"trashed keep")
    write(active, "saves/slot1.sav", b"recreated save")
    write(active, "other.txt", b"recreated other")

    game_trash.restore_game_from_trash(active, root, GAME_ID)

    assert not trash.exists()
    assert (active / "saves" / "slot1.sav").read_bytes() == b"trashed save"
    assert (active / "saves" / "slot1 (preserved-1).sav").read_bytes() == b"recreated save"
    assert (active / "other.txt").read_bytes() == b"recreated other"
    assert (active / "keep.txt").read_bytes() == b"trashed keep"


def test_nested_directories_merge_and_nested_collisions_are_preserved(dirs):
    active, trash, root = dirs
    write(trash, "files/doc/a.txt", b"trash a")
    write(trash, "files/doc/b.txt", b"trash b")
    write(trash, "_trash/doc/child/x.bin", b"nested trash old")
    write(active, "files/doc/a.txt", b"active a")
    write(active, "files/doc/c.txt", b"active c")
    write(active, "_trash/doc/child/x.bin", b"nested trash new")

    game_trash.move_game_to_trash(active, root, GAME_ID)

    assert tree(trash) == {
        "files/doc/a.txt": b"active a",
        "files/doc/a (preserved-1).txt": b"trash a",
        "files/doc/b.txt": b"trash b",
        "files/doc/c.txt": b"active c",
        "_trash/doc/child/x.bin": b"nested trash new",
        "_trash/doc/child/x (preserved-1).bin": b"nested trash old",
    }


def test_suffix_collisions_pick_the_next_free_name(dirs):
    active, trash, root = dirs
    write(trash, "a.txt", b"v1")
    write(trash, "a (preserved-1).txt", b"v-already-preserved")
    write(trash, "a (preserved-2).txt", b"v-also-preserved")
    write(active, "a.txt", b"v2")

    game_trash.move_game_to_trash(active, root, GAME_ID)

    assert tree(trash) == {
        "a.txt": b"v2",
        "a (preserved-1).txt": b"v-already-preserved",
        "a (preserved-2).txt": b"v-also-preserved",
        "a (preserved-3).txt": b"v1",
    }


def test_file_versus_directory_collisions_keep_both(dirs):
    active, trash, root = dirs
    write(trash, "clash/inside.txt", b"directory in trash")
    write(trash, "flip", b"file in trash")
    write(active, "clash", b"file in active")
    write(active, "flip/inside.txt", b"directory in active")

    game_trash.move_game_to_trash(active, root, GAME_ID)

    assert tree(trash) == {
        "clash": b"file in active",
        "clash (preserved-1)/inside.txt": b"directory in trash",
        "flip/inside.txt": b"directory in active",
        "flip (preserved-1)": b"file in trash",
    }


def test_identical_files_are_not_duplicated(dirs):
    active, trash, root = dirs
    write(trash, "same.txt", b"same")
    write(active, "same.txt", b"same")
    game_trash.move_game_to_trash(active, root, GAME_ID)
    assert tree(trash) == {"same.txt": b"same"}
    assert not active.exists()


def test_repeated_moves_are_idempotent(dirs):
    active, trash, root = dirs
    write(active, "a.txt", b"a")
    game_trash.move_game_to_trash(active, root, GAME_ID)
    before = tree(trash)
    game_trash.move_game_to_trash(active, root, GAME_ID)
    game_trash.restore_game_from_trash(active, root, GAME_ID)
    game_trash.restore_game_from_trash(active, root, GAME_ID)
    assert tree(active) == before and not trash.exists()


def test_restore_without_trash_changes_nothing(dirs):
    active, _trash, root = dirs
    write(active, "a.txt", b"a")
    game_trash.restore_game_from_trash(active, root, GAME_ID)
    assert tree(active) == {"a.txt": b"a"}


def test_repeated_import_delete_restore_cycles_lose_nothing(dirs):
    active, trash, root = dirs
    expected: set[bytes] = set()
    for cycle in range(6):
        payloads = {
            "library/cover.png": f"cover {cycle}".encode(),
            f"cycle-{cycle}.txt": f"only {cycle}".encode(),
            "saves/slot.sav": f"save {cycle}".encode(),
        }
        for relative, data in payloads.items():
            write(active, relative, data)
            expected.add(data)
        game_trash.move_game_to_trash(active, root, GAME_ID)
        assert not active.exists()
    # a fresh folder appears (new import) while the old game is still in trash
    write(active, "library/cover.png", b"brand new cover")
    expected.add(b"brand new cover")
    game_trash.restore_game_from_trash(active, root, GAME_ID)
    game_trash.restore_game_from_trash(active, root, GAME_ID)
    assert not trash.exists()
    assert set(all_bytes(active)) == expected
    assert len(all_bytes(active)) == len(expected)


@pytest.mark.parametrize("fail_on", range(1, 9))
def test_interrupted_move_can_be_rerun_without_loss(dirs, monkeypatch, fail_on):
    active, trash, root = dirs
    for name in ("a.txt", "b.txt", "d/c.txt", "d/e.txt"):
        write(active, name, f"active {name}".encode())
        write(trash, name, f"trash {name}".encode())
    write(trash, "t-only.txt", b"t only")
    expected = set(all_bytes(active)) | set(all_bytes(trash))

    real_rename, calls = game_trash._rename_noreplace, {"n": 0}

    def flaky(source, dest, *args, **kwargs):
        calls["n"] += 1
        if calls["n"] == fail_on:
            raise OSError(errno.EIO, "injected")
        return real_rename(source, dest, *args, **kwargs)

    monkeypatch.setattr(game_trash, "_rename_noreplace", flaky)
    with pytest.raises(OSError):
        game_trash.move_game_to_trash(active, root, GAME_ID)
    monkeypatch.setattr(game_trash, "_rename_noreplace", real_rename)

    game_trash.move_game_to_trash(active, root, GAME_ID)

    assert not active.exists()
    assert set(all_bytes(trash)) == expected
    assert len(all_bytes(trash)) == len(expected)


def test_crash_after_cross_device_copy_is_deduplicated_on_rerun(dirs, monkeypatch):
    active, trash, root = dirs
    write(active, "big.bin", b"payload")
    real_rename, real_unlink = game_trash._rename_noreplace, Path.unlink

    def other_device(source, dest, *args, **kwargs):
        if Path(source) == active / "big.bin" or Path(source) == active:
            raise OSError(errno.EXDEV, "cross-device")
        return real_rename(source, dest, *args, **kwargs)

    def die_before_unlink(self, *args, **kwargs):
        if self == active / "big.bin":
            raise OSError(errno.EIO, "crash")
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(game_trash, "_rename_noreplace", other_device)
    monkeypatch.setattr(Path, "unlink", die_before_unlink)
    with pytest.raises(OSError):
        game_trash.move_game_to_trash(active, root, GAME_ID)
    # both whole copies exist, no partial file is visible under its real name
    assert (active / "big.bin").read_bytes() == b"payload"
    assert (trash / "big.bin").read_bytes() == b"payload"
    monkeypatch.setattr(Path, "unlink", real_unlink)

    game_trash.move_game_to_trash(active, root, GAME_ID)

    assert tree(trash) == {"big.bin": b"payload"} and not active.exists()


def test_symlinked_or_file_destinations_are_refused_untouched(dirs, tmp_path):
    active, trash, root = dirs
    write(active, "a.txt", b"a")
    outside = tmp_path / "outside"
    write(outside, "precious.txt", b"precious")
    trash.parent.mkdir(parents=True)
    try:
        trash.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks need extra privileges on this platform")
    with pytest.raises(game_folders.FolderStorageError):
        game_trash.move_game_to_trash(active, root, GAME_ID)
    assert tree(outside) == {"precious.txt": b"precious"}
    assert tree(active) == {"a.txt": b"a"}


def test_a_file_in_place_of_the_destination_folder_is_refused(dirs):
    active, trash, root = dirs
    write(active, "a.txt", b"a")
    write(root, f"_trash/{GAME_ID}", b"a file squatting on the trash name")
    with pytest.raises(game_folders.FolderStorageError):
        game_trash.move_game_to_trash(active, root, GAME_ID)
    assert tree(active) == {"a.txt": b"a"} and trash.read_bytes().startswith(b"a file")


def test_undo_moves_reverses_a_merge_without_touching_foreign_data(dirs):
    active, trash, root = dirs
    write(trash, "shot.png", b"from trash")
    write(trash, "same.txt", b"same")
    write(trash, "deep/n.md", b"note")
    write(active, "shot.png", b"foreign")
    write(active, "same.txt", b"same")
    write(active, "unrelated.txt", b"unrelated")
    journal: game_trash.Journal = []

    game_trash.restore_game_from_trash(active, root, GAME_ID, journal)
    assert not trash.exists()
    game_trash.undo_moves(journal)

    assert tree(trash) == {"shot.png": b"from trash", "same.txt": b"same", "deep/n.md": b"note"}
    assert tree(active) == {
        "shot.png": b"foreign",
        "same.txt": b"same",
        "unrelated.txt": b"unrelated",
    }


def test_undo_moves_of_a_plain_move_returns_the_whole_folder(dirs):
    active, trash, root = dirs
    write(trash, "a/b.txt", b"b")
    journal: game_trash.Journal = []
    game_trash.restore_game_from_trash(active, root, GAME_ID, journal)
    game_trash.undo_moves(journal)
    assert tree(trash) == {"a/b.txt": b"b"}
    assert not active.exists()


def test_restore_reports_whether_anything_was_moved(dirs):
    active, _, root = dirs
    assert game_trash.restore_game_from_trash(active, root, GAME_ID) is False


@pytest.mark.skipif(
    game_trash._RENAMEAT2 is None,  # pylint: disable=protected-access
    reason="renameat2 with RENAME_NOREPLACE exists only on Linux",
)
def test_a_destination_that_appears_during_the_move_is_never_replaced(dirs, monkeypatch):
    active, _, _ = dirs
    source = write(active, "from.txt", b"mover")
    dest = write(active, "to.txt", b"already here")
    monkeypatch.setattr(game_trash, "_exists", lambda _path: False)  # the check was too early

    with pytest.raises(game_folders.FolderStorageError):
        game_trash._place(source, dest)  # pylint: disable=protected-access

    assert source.read_bytes() == b"mover"
    assert dest.read_bytes() == b"already here"


# ------------------------------------------------------------------ routes and sweep


@pytest.fixture(autouse=True)
def sweep_storage(tmp_path, monkeypatch, caplog):
    """Point the sweep at the same temporary data root the routes use, and observe its warnings."""
    monkeypatch.setattr(save_game_asset, "DATA_ROOT", tmp_path)
    monkeypatch.setattr(sweep.logger, "disabled", False)
    caplog.set_level(logging.WARNING, logger=sweep.__name__)


def active_folder(flow) -> Path:
    return flow.tmp / str(flow.user_id) / "games" / "Flow"


def trash_folder(flow) -> Path:
    return game_trash.game_trash_dir(flow.tmp, flow.game_id)


async def deleted_at(flow):
    async with SessionLocal() as db:
        row = await db.get(Game, flow.game_id, populate_existing=True)
        return row.deleted_at


async def delete(flow):
    return await flow.client.delete(f"/api/game/delete/{flow.game_id}")


async def restore(flow):
    return await flow.client.post(f"/api/game/{flow.game_id}/restore")


def fail_commit(monkeypatch, *, after_commit: bool = False):
    real, armed = AsyncSession.commit, {"on": True}

    async def commit(self):
        if armed["on"]:
            armed["on"] = False
            if after_commit:
                await real(self)
            raise RuntimeError("commit failed")
        return await real(self)

    monkeypatch.setattr(AsyncSession, "commit", commit)


async def test_delete_and_restore_round_trip_keeps_rows_and_bytes(flow):
    write(active_folder(flow), "notes/n.md", b"note")
    assert (await delete(flow)).status_code == 204
    assert await deleted_at(flow) is not None
    assert tree(trash_folder(flow)) == {"notes/n.md": b"note"}
    assert (await restore(flow)).status_code == 200
    assert await deleted_at(flow) is None
    assert tree(active_folder(flow)) == {"notes/n.md": b"note"}
    assert not trash_folder(flow).exists()


async def test_delete_over_a_leftover_trash_folder_keeps_both(flow):
    write(trash_folder(flow), "shot.png", b"leftover")
    write(active_folder(flow), "shot.png", b"live")
    assert (await delete(flow)).status_code == 204
    assert tree(trash_folder(flow)) == {
        "shot.png": b"live",
        "shot (preserved-1).png": b"leftover",
    }
    assert await deleted_at(flow) is not None


async def test_restore_over_a_recreated_folder_keeps_both(flow):
    write(active_folder(flow), "shot.png", b"original")
    assert (await delete(flow)).status_code == 204
    write(active_folder(flow), "shot.png", b"recreated")
    write(active_folder(flow), "extra.txt", b"extra")
    assert (await restore(flow)).status_code == 200
    assert tree(active_folder(flow)) == {
        "shot.png": b"original",
        "shot (preserved-1).png": b"recreated",
        "extra.txt": b"extra",
    }
    assert not trash_folder(flow).exists()
    assert await deleted_at(flow) is None


async def test_repeated_delete_restore_cycles_through_the_routes(flow):
    seen = {b"cycle 0"}
    write(active_folder(flow), "same-name.txt", b"cycle 0")
    for cycle in range(1, 5):
        assert (await delete(flow)).status_code == 204
        data = f"cycle {cycle}".encode()
        write(active_folder(flow), "same-name.txt", data)  # a re-import recreates the folder
        seen.add(data)
        assert (await restore(flow)).status_code == 200
    assert set(all_bytes(active_folder(flow))) == seen
    assert len(all_bytes(active_folder(flow))) == len(seen)
    assert not trash_folder(flow).exists()
    assert await deleted_at(flow) is None


async def test_failed_commit_on_delete_moves_everything_back(flow, monkeypatch):
    write(active_folder(flow), "a/b.txt", b"b")
    fail_commit(monkeypatch)
    with pytest.raises(RuntimeError):
        await delete(flow)
    assert await deleted_at(flow) is None
    assert tree(active_folder(flow)) == {"a/b.txt": b"b"}
    assert not trash_folder(flow).exists()
    assert (await delete(flow)).status_code == 204  # and the retry works
    assert tree(trash_folder(flow)) == {"a/b.txt": b"b"}


async def test_lost_commit_acknowledgement_on_delete_keeps_the_committed_state(flow, monkeypatch):
    write(active_folder(flow), "a/b.txt", b"b")
    fail_commit(monkeypatch, after_commit=True)
    with pytest.raises(RuntimeError):
        await delete(flow)
    assert await deleted_at(flow) is not None
    assert tree(trash_folder(flow)) == {"a/b.txt": b"b"}
    assert not active_folder(flow).exists()


async def test_failed_commit_on_restore_moves_everything_back_to_trash(flow, monkeypatch):
    write(active_folder(flow), "a/b.txt", b"b")
    assert (await delete(flow)).status_code == 204
    fail_commit(monkeypatch)
    with pytest.raises(RuntimeError):
        await restore(flow)
    assert await deleted_at(flow) is not None
    assert tree(trash_folder(flow)) == {"a/b.txt": b"b"}
    assert not active_folder(flow).exists()
    assert (await restore(flow)).status_code == 200
    assert tree(active_folder(flow)) == {"a/b.txt": b"b"}


async def test_failed_move_on_delete_is_retried_to_completion(flow, monkeypatch):
    for name in ("a.txt", "b.txt", "c.txt"):
        write(active_folder(flow), name, name.encode())
        write(trash_folder(flow), name, b"old " + name.encode())
    real_rename, calls = game_trash._rename_noreplace, {"n": 0}

    def flaky(source, dest, *args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 4:
            raise OSError(errno.EIO, "injected")
        return real_rename(source, dest, *args, **kwargs)

    monkeypatch.setattr(game_trash, "_rename_noreplace", flaky)
    with pytest.raises(OSError):
        await delete(flow)
    monkeypatch.setattr(game_trash, "_rename_noreplace", real_rename)
    # the failure path moved what it could back; whatever state, nothing is lost
    assert await deleted_at(flow) is None
    assert (await delete(flow)).status_code == 204
    names = set(all_bytes(trash_folder(flow)))
    assert names == {b"a.txt", b"b.txt", b"c.txt", b"old a.txt", b"old b.txt", b"old c.txt"}


async def test_sweep_recovers_a_delete_that_died_before_its_commit(flow):
    write(active_folder(flow), "a/b.txt", b"b")
    # the process died after the move and before the commit: row active, data in trash
    game_trash.move_game_to_trash(active_folder(flow), flow.tmp, flow.game_id)
    assert await deleted_at(flow) is None and not active_folder(flow).exists()

    await sweep.purge_expired_trash()

    assert tree(active_folder(flow)) == {"a/b.txt": b"b"}
    assert not trash_folder(flow).exists()
    assert await deleted_at(flow) is None


async def test_sweep_recovery_keeps_both_copies_for_an_active_game(flow):
    write(trash_folder(flow), "keep.txt", b"keep")
    write(active_folder(flow), "keep.txt", b"live")
    await sweep.purge_expired_trash()
    # recovery brings the trashed copy home under its own name and keeps the live one beside it
    assert tree(active_folder(flow)) == {"keep.txt": b"keep", "keep (preserved-1).txt": b"live"}
    assert not trash_folder(flow).exists()


async def expire(flow, **changes):
    async with SessionLocal() as db:
        row = await db.get(Game, flow.game_id)
        row.deleted_at = 1  # long past the retention window
        for name, value in changes.items():
            setattr(row, name, value)
        await db.commit()


async def row_exists(flow) -> bool:
    async with SessionLocal() as db:
        return await db.get(Game, flow.game_id, populate_existing=True) is not None


async def test_sweep_holds_the_purge_while_the_active_folder_holds_restored_data(flow, caplog):
    write(active_folder(flow), "a.txt", b"a")
    assert (await delete(flow)).status_code == 204
    # a restore died after moving the data home and before its commit: row deleted, data active
    game_trash.restore_game_from_trash(active_folder(flow), flow.tmp, flow.game_id)
    await expire(flow)

    counts = await sweep.purge_expired_trash()

    assert counts["purged_games"] == 0
    assert await row_exists(flow)
    assert tree(active_folder(flow)) == {"a.txt": b"a"}
    assert "still has data in its active folder" in caplog.text
    # the interrupted restore can simply be finished
    assert (await restore(flow)).status_code == 200
    assert tree(active_folder(flow)) == {"a.txt": b"a"}


async def test_sweep_purges_once_the_stranded_folder_is_gone(flow):
    write(active_folder(flow), "a.txt", b"a")
    assert (await delete(flow)).status_code == 204
    write(active_folder(flow), "stranded.txt", b"stranded")
    await expire(flow)
    assert (await sweep.purge_expired_trash())["purged_games"] == 0
    shutil.rmtree(active_folder(flow))  # an administrator dealt with it
    assert (await sweep.purge_expired_trash())["purged_games"] == 1
    assert not await row_exists(flow)
    assert not trash_folder(flow).exists()


async def test_sweep_purge_ignores_an_empty_active_folder(flow):
    write(active_folder(flow), "a.txt", b"a")
    assert (await delete(flow)).status_code == 204
    active_folder(flow).mkdir(parents=True)
    await expire(flow)
    assert (await sweep.purge_expired_trash())["purged_games"] == 1
    assert not trash_folder(flow).exists()


async def test_sweep_purge_ignores_a_folder_another_active_game_owns(flow):
    write(active_folder(flow), "a.txt", b"a")
    assert (await delete(flow)).status_code == 204
    async with SessionLocal() as db:
        db.add(
            Game(user_id=flow.user_id, title="Other", sort_title="Other", folder_location="Flow")
        )
        await db.commit()
    write(active_folder(flow), "theirs.txt", b"theirs")
    await expire(flow)

    assert (await sweep.purge_expired_trash())["purged_games"] == 1
    assert tree(active_folder(flow)) == {"theirs.txt": b"theirs"}
    assert not trash_folder(flow).exists()


async def test_sweep_purges_a_game_whose_legacy_folder_name_is_not_a_valid_folder(flow):
    write(active_folder(flow), "a.txt", b"a")
    assert (await delete(flow)).status_code == 204
    await expire(flow, folder_location="Legacy Name With Spaces")

    assert (await sweep.purge_expired_trash())["purged_games"] == 1
    assert not await row_exists(flow)
    assert not trash_folder(flow).exists()


async def test_failed_restore_over_a_recreated_folder_keeps_foreign_data_out_of_trash(
    flow, monkeypatch
):
    write(active_folder(flow), "shot.png", b"original")
    assert (await delete(flow)).status_code == 204
    write(active_folder(flow), "shot.png", b"recreated")
    write(active_folder(flow), "unrelated.txt", b"unrelated")
    fail_commit(monkeypatch)

    with pytest.raises(RuntimeError):
        await restore(flow)

    assert await deleted_at(flow) is not None
    assert tree(trash_folder(flow)) == {"shot.png": b"original"}
    assert tree(active_folder(flow)) == {"shot.png": b"recreated", "unrelated.txt": b"unrelated"}

    # retention at the game's original expiry must not take the unrelated data with it
    await expire(flow)
    assert (await sweep.purge_expired_trash())["purged_games"] == 0
    assert tree(trash_folder(flow)) == {"shot.png": b"original"}
    assert tree(active_folder(flow)) == {"shot.png": b"recreated", "unrelated.txt": b"unrelated"}
    assert await row_exists(flow)

    # and the restore still works afterwards, keeping everything
    assert (await restore(flow)).status_code == 200
    assert tree(active_folder(flow)) == {
        "shot.png": b"original",
        "shot (preserved-1).png": b"recreated",
        "unrelated.txt": b"unrelated",
    }


async def test_failed_restore_move_over_a_recreated_folder_undoes_only_its_own_steps(
    flow, monkeypatch
):
    for name in ("a.txt", "b.txt", "c.txt"):
        write(active_folder(flow), name, b"old " + name.encode())
    assert (await delete(flow)).status_code == 204
    write(active_folder(flow), "b.txt", b"new b")
    write(active_folder(flow), "extra.txt", b"extra")
    real_place, calls = game_trash._place, {"n": 0}  # pylint: disable=protected-access

    def flaky(source, dest, journal=None):
        calls["n"] += 1
        if calls["n"] == 3:
            raise OSError(errno.EIO, "injected")
        return real_place(source, dest, journal)

    monkeypatch.setattr(game_trash, "_place", flaky)
    with pytest.raises(OSError):
        await restore(flow)
    monkeypatch.setattr(game_trash, "_place", real_place)

    assert await deleted_at(flow) is not None
    assert tree(trash_folder(flow)) == {
        "a.txt": b"old a.txt",
        "b.txt": b"old b.txt",
        "c.txt": b"old c.txt",
    }
    assert tree(active_folder(flow)) == {"b.txt": b"new b", "extra.txt": b"extra"}
    assert (await restore(flow)).status_code == 200
    assert set(all_bytes(active_folder(flow))) == {
        b"old a.txt",
        b"old b.txt",
        b"old c.txt",
        b"new b",
        b"extra",
    }


async def test_recover_does_not_count_a_folder_that_a_restore_already_took(
    flow, monkeypatch, caplog
):
    write(active_folder(flow), "a.txt", b"a")
    # the scan sees a trash folder, but by the time the lock is held it is gone
    monkeypatch.setattr(game_trash, "_trashed_game_ids", lambda _root: [flow.game_id])
    async with SessionLocal() as db:
        assert await game_trash.recover_interrupted_deletes(db, flow.tmp) == 0
    assert "Recovered game" not in caplog.text
    assert tree(active_folder(flow)) == {"a.txt": b"a"}


async def test_recover_keeps_going_after_an_unexpected_error_for_one_game(flow, monkeypatch):
    write(active_folder(flow), "a.txt", b"a")
    game_trash.move_game_to_trash(active_folder(flow), flow.tmp, flow.game_id)
    stray = uuid.uuid4()
    write(game_trash.game_trash_dir(flow.tmp, stray), "x.txt", b"x")
    real = game_trash._current_row  # pylint: disable=protected-access

    async def broken(db, game_id):
        if game_id == stray:
            raise RuntimeError("database hiccup")
        return await real(db, game_id)

    monkeypatch.setattr(game_trash, "_current_row", broken)
    async with SessionLocal() as db:
        assert await game_trash.recover_interrupted_deletes(db, flow.tmp) == 1
    assert tree(active_folder(flow)) == {"a.txt": b"a"}
    assert tree(game_trash.game_trash_dir(flow.tmp, stray)) == {"x.txt": b"x"}


async def test_game_created_over_a_trashed_folder_name_through_the_routes_loses_nothing(flow):
    write(active_folder(flow), "mine.txt", b"mine")
    assert (await delete(flow)).status_code == 204
    # a real create reuses the name while the first game sits in trash
    created = await flow.client.post(
        "/api/game/create", json={"title": "Second", "folder_location": "Flow"}
    )
    assert created.status_code == 201
    second = created.json()["id"]
    write(active_folder(flow), "second.txt", b"second")
    assert (await flow.client.delete(f"/api/game/delete/{second}")).status_code == 204
    second_trash = game_trash.game_trash_dir(flow.tmp, uuid.UUID(second))
    assert tree(second_trash) == {"second.txt": b"second"}

    assert (await restore(flow)).status_code == 200
    assert tree(active_folder(flow)) == {"mine.txt": b"mine"}
    # the second game cannot take the name back while the first owns it, and keeps its bytes
    assert (await flow.client.post(f"/api/game/{second}/restore")).status_code == 409
    assert tree(second_trash) == {"second.txt": b"second"}
    assert tree(active_folder(flow)) == {"mine.txt": b"mine"}


async def test_concurrent_delete_and_interrupted_delete_recovery_lose_nothing(flow):
    write(active_folder(flow), "a.txt", b"a")

    async def recover():
        async with SessionLocal() as db:
            return await game_trash.recover_interrupted_deletes(db, flow.tmp)

    for _ in range(5):
        deleted, _recovered = await asyncio.gather(delete(flow), recover())
        assert deleted.status_code == 204
        # whichever ran first, the row and the bytes agree on where the game is
        assert await deleted_at(flow) is not None
        assert tree(trash_folder(flow)) == {"a.txt": b"a"}
        assert not active_folder(flow).exists()
        assert (await restore(flow)).status_code == 200
        assert tree(active_folder(flow)) == {"a.txt": b"a"}


async def test_restore_is_refused_when_another_active_game_owns_the_folder(flow):
    write(active_folder(flow), "mine.txt", b"mine")
    assert (await delete(flow)).status_code == 204
    async with SessionLocal() as db:
        other = Game(
            user_id=flow.user_id, title="Other", sort_title="Other", folder_location="Flow"
        )
        db.add(other)
        await db.commit()
    write(active_folder(flow), "theirs.txt", b"theirs")

    response = await restore(flow)

    assert response.status_code == 409
    assert tree(trash_folder(flow)) == {"mine.txt": b"mine"}
    assert tree(active_folder(flow)) == {"theirs.txt": b"theirs"}
    assert await deleted_at(flow) is not None


async def test_another_users_folder_with_the_same_name_is_never_touched(flow):
    stranger = uuid.uuid4()
    other_dir = flow.tmp / str(stranger) / "games" / "Flow"
    write(other_dir, "theirs.txt", b"theirs")
    write(active_folder(flow), "mine.txt", b"mine")
    assert (await delete(flow)).status_code == 204
    assert (await restore(flow)).status_code == 200
    assert tree(other_dir) == {"theirs.txt": b"theirs"}
    assert tree(active_folder(flow)) == {"mine.txt": b"mine"}


async def test_symlinked_games_root_is_refused_with_a_conflict(flow, tmp_path):
    outside = tmp_path.parent / f"outside-{uuid.uuid4().hex}"
    write(outside, "precious.txt", b"precious")
    games_root = flow.tmp / str(flow.user_id) / "games"
    games_root.mkdir(parents=True, exist_ok=True)
    try:
        games_root.rmdir()
        games_root.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks need extra privileges on this platform")
    response = await delete(flow)
    assert response.status_code == 409
    assert tree(outside) == {"precious.txt": b"precious"}
    assert await deleted_at(flow) is None
