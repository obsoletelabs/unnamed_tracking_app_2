"""Deleting an account removes that account's files and only those (issue #471)."""

from __future__ import annotations

import os
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from src.core.config import settings
from src.database.models.admin_user_audit import AdminUserAudit
from src.database.models.game import Game
from src.database.models.user import User
from src.database.session import SessionLocal
from src.features import account_storage, profile_pictures
from src.helpers import save_game_asset
from tests.test_admin_users import accounts as accounts  # noqa: F401 - fixture
from tests.test_admin_users import api


@pytest.fixture
def roots(monkeypatch, tmp_path):
    """A data root that is not the default /data, with the profile and game roots apart."""
    data = tmp_path / "custom-data"
    profile_root, game_root = data / "user", data / "users"
    profile_root.mkdir(parents=True)
    game_root.mkdir()
    monkeypatch.setattr(profile_pictures, "_USER_DATA_ROOT", profile_root)
    monkeypatch.setattr(save_game_asset, "DATA_ROOT", game_root)
    return profile_root, game_root


def _populate(user_id: UUID, profile_root, game_root, game_ids=()) -> dict[str, bytes]:
    """Write a realistic tree for one account and return every file's content by path."""
    written: dict[str, bytes] = {}

    def put(path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        written[str(path)] = data

    put(profile_root / str(user_id) / "profile.png", b"profile-" + user_id.bytes)
    put(profile_root / str(user_id) / "inbox" / "screenshot" / "shot.png", b"inbox")
    game = game_root / str(user_id) / "games" / "Some Game"
    put(game / "key_art.png", b"art-" + user_id.bytes)
    put(game / "notes" / "n.md", b"notes")
    put(game / "screenshots" / "s.png", b"shot")
    put(game / "clips" / "c.mp4", b"clip")
    put(game / "_trash" / "screenshot" / "old.png", b"trashed")
    for game_id in game_ids:
        put(game_root / "_trash" / str(game_id) / "folder" / "kept.txt", b"trash-" + game_id.bytes)
    return written


async def _add_game(user_id: UUID, title: str) -> UUID:
    game = Game(id=uuid4(), user_id=user_id, title=title, sort_title=title, folder_location=title)
    async with SessionLocal() as db:
        db.add(game)
        await db.commit()
    return game.id


async def _user_exists(user_id: UUID) -> bool:
    async with SessionLocal() as db:
        return await db.get(User, user_id) is not None


async def _game_count(user_id: UUID) -> int:
    async with SessionLocal() as db:
        return len(list(await db.scalars(select(Game.id).where(Game.user_id == user_id))))


async def _audits(admin_id: UUID, target_id: UUID) -> list[tuple[str, str]]:
    async with SessionLocal() as db:
        rows = await db.scalars(
            select(AdminUserAudit)
            .where(AdminUserAudit.actor_id == admin_id, AdminUserAudit.target_id == target_id)
            .order_by(AdminUserAudit.occurred_at, AdminUserAudit.id)
        )
        return [(row.action, row.outcome) for row in rows]


async def _other_user() -> UUID:
    other = uuid4()
    async with SessionLocal() as db:
        db.add(
            User(
                id=other,
                username="oth" + other.hex[:12],
                email=f"{other}@example.test",
                password_hash="unused",
            )
        )
        await db.commit()
    return other


async def _drop_user(user_id: UUID) -> None:
    async with SessionLocal() as db:
        user = await db.get(User, user_id)
        if user is not None:
            await db.delete(user)
            await db.commit()


def _assert_untouched(files: dict[str, bytes]) -> None:
    for path, data in files.items():
        with open(path, "rb") as handle:
            assert handle.read() == data, path


@pytest.mark.asyncio
async def test_deleting_a_populated_account_removes_its_files_and_only_its_files(accounts, roots):
    profile_root, game_root = roots
    a = accounts
    other = await _other_user()
    try:
        game_id = await _add_game(a.target, "Some Game")
        other_game = await _add_game(other, "Some Game")
        await _add_game(a.target, "Second")  # a game with no folder on disk
        mine = _populate(a.target, profile_root, game_root, [game_id])
        theirs = _populate(other, profile_root, game_root, [other_game])
        unrelated = game_root / "_trash" / str(uuid4()) / "x.txt"
        unrelated.parent.mkdir(parents=True)
        unrelated.write_bytes(b"another game's trash")

        async with api(a.admin, a.admin_name) as client:
            response = await client.delete(f"/api/auth/users/{a.target}")

        assert response.status_code == 200
        assert response.json() == {"status": "deleted", "user_id": str(a.target)}
        # Database state.
        assert not await _user_exists(a.target)
        assert await _game_count(a.target) == 0
        assert await _user_exists(other)
        assert await _game_count(other) == 1
        assert ("account.delete", "success") in await _audits(a.admin, a.target)
        # Disk state: every one of the account's files is gone ...
        assert not any(os.path.lexists(path) for path in mine)
        assert not (profile_root / str(a.target)).exists()
        assert not (game_root / str(a.target)).exists()
        assert not (game_root / "_trash" / str(game_id)).exists()
        # ... and nothing else was touched.
        _assert_untouched(theirs)
        assert unrelated.read_bytes() == b"another game's trash"
        assert (game_root / "_trash").is_dir() and profile_root.is_dir() and game_root.is_dir()
    finally:
        await _drop_user(other)


@pytest.mark.asyncio
async def test_an_account_without_any_files_deletes_cleanly(accounts, roots):
    async with api(accounts.admin, accounts.admin_name) as client:
        response = await client.delete(f"/api/auth/users/{accounts.target}")
    assert response.status_code == 200
    assert response.json()["status"] == "deleted"
    assert not await _user_exists(accounts.target)


@pytest.mark.asyncio
async def test_a_file_failure_still_deletes_the_account_and_a_retry_finishes_the_cleanup(
    accounts, roots, monkeypatch, caplog
):
    profile_root, game_root = roots
    a = accounts
    mine = _populate(a.target, profile_root, game_root)
    real_rmtree = account_storage._rmtree

    def deny_game_media(path):
        if path.parent == game_root:
            raise PermissionError("denied")
        real_rmtree(path)

    monkeypatch.setattr(account_storage, "_rmtree", deny_game_media)
    async with api(a.admin, a.admin_name) as client:
        first = await client.delete(f"/api/auth/users/{a.target}")
        assert first.status_code == 200
        body = first.json()
        assert body["status"] == "deleted" and body["storage"] == "incomplete"
        assert "retry" in body["detail"].lower()
        assert str(game_root) not in str(body)  # no filesystem paths in the response
        # The database delete stands; the profile files are gone; the denied tree remains.
        assert not await _user_exists(a.target)
        assert not (profile_root / str(a.target)).exists()
        assert (game_root / str(a.target) / "games" / "Some Game" / "key_art.png").exists()
        audits = await _audits(a.admin, a.target)
        assert ("account.delete", "success") in audits
        assert ("account.delete.storage", "failure") in audits
        assert any("cleanup failed" in r.getMessage() for r in caplog.records)

        # The operator fixes the permissions and repeats the deletion.
        monkeypatch.setattr(account_storage, "_rmtree", real_rmtree)
        second = await client.delete(f"/api/auth/users/{a.target}")
        assert second.status_code == 200
        assert second.json()["status"] == "storage_cleaned"
        third = await client.delete(f"/api/auth/users/{a.target}")
        assert third.status_code == 404  # fully idempotent: nothing is left to do
    assert not any(os.path.lexists(path) for path in mine)
    assert not (game_root / str(a.target)).exists()


@pytest.mark.asyncio
async def test_one_failing_path_does_not_stop_the_other_cleanups(accounts, roots, monkeypatch):
    profile_root, game_root = roots
    game_id = await _add_game(accounts.target, "Some Game")
    _populate(accounts.target, profile_root, game_root, [game_id])
    real_rmtree = account_storage._rmtree

    def deny_profile(path):
        if path.parent == profile_root:
            raise PermissionError("denied")
        real_rmtree(path)

    monkeypatch.setattr(account_storage, "_rmtree", deny_profile)
    async with api(accounts.admin, accounts.admin_name) as client:
        response = await client.delete(f"/api/auth/users/{accounts.target}")
    assert response.json()["storage"] == "incomplete"
    assert not (game_root / str(accounts.target)).exists()
    assert not (game_root / "_trash" / str(game_id)).exists()
    assert (profile_root / str(accounts.target) / "profile.png").exists()


@pytest.mark.asyncio
async def test_a_failed_trash_folder_is_found_and_removed_by_a_retry_with_audit(
    accounts, roots, monkeypatch
):
    profile_root, game_root = roots
    game_id = await _add_game(accounts.target, "Some Game")
    _populate(accounts.target, profile_root, game_root, [game_id])
    trash = game_root / "_trash" / str(game_id)
    real_rmtree = account_storage._rmtree

    def deny_trash(path):
        if path == trash:
            raise PermissionError("denied")
        real_rmtree(path)

    monkeypatch.setattr(account_storage, "_rmtree", deny_trash)
    async with api(accounts.admin, accounts.admin_name) as client:
        first = await client.delete(f"/api/auth/users/{accounts.target}")
        assert first.json()["storage"] == "incomplete"
        assert trash.exists()
        assert await _game_count(accounts.target) == 0  # the game row is gone for good

        # The retry still failing is audited and reported, and keeps the folder findable.
        failing = await client.delete(f"/api/auth/users/{accounts.target}")
        assert failing.status_code == 500
        assert trash.exists()
        audits = await _audits(accounts.admin, accounts.target)
        assert audits.count(("account.delete.storage", "failure")) == 2

        monkeypatch.setattr(account_storage, "_rmtree", real_rmtree)
        second = await client.delete(f"/api/auth/users/{accounts.target}")
        assert second.status_code == 200 and second.json()["status"] == "storage_cleaned"
        assert not trash.exists()
        assert ("account.delete.storage", "success") in await _audits(
            accounts.admin, accounts.target
        )
        third = await client.delete(f"/api/auth/users/{accounts.target}")
        assert third.status_code == 404


@pytest.mark.asyncio
async def test_a_failed_audit_commit_after_the_delete_still_reports_incomplete(
    accounts, roots, monkeypatch
):
    profile_root, game_root = roots
    _populate(accounts.target, profile_root, game_root)
    real_rmtree = account_storage._rmtree

    def deny(path):
        if path.parent == profile_root:
            raise PermissionError("denied")
        real_rmtree(path)

    monkeypatch.setattr(account_storage, "_rmtree", deny)
    from sqlalchemy.ext.asyncio import AsyncSession

    real_commit = AsyncSession.commit
    calls = {"n": 0}

    async def flaky_commit(self):
        calls["n"] += 1
        if calls["n"] > 1 and any(
            isinstance(o, AdminUserAudit) and o.action.startswith("account.delete.storage")
            for o in self.new
        ):
            raise RuntimeError("audit store down")
        return await real_commit(self)

    monkeypatch.setattr(AsyncSession, "commit", flaky_commit)
    async with api(accounts.admin, accounts.admin_name) as client:
        response = await client.delete(f"/api/auth/users/{accounts.target}")
    assert response.status_code == 200
    assert response.json()["storage"] == "incomplete"
    assert not await _user_exists(accounts.target)


@pytest.mark.asyncio
async def test_a_refused_delete_leaves_every_file_in_place(accounts, roots):
    """Files go only after the commit: the primary user is refused before anything is touched."""
    profile_root, game_root = roots
    async with SessionLocal() as db:
        user = await db.get(User, accounts.target)
        original = user.username
        user.username = settings.PRIMARY_USER_USERNAME
        await db.commit()
    try:
        mine = _populate(accounts.target, profile_root, game_root)
        async with api(accounts.admin, accounts.admin_name) as client:
            response = await client.delete(f"/api/auth/users/{accounts.target}")
        assert response.status_code == 400
        assert await _user_exists(accounts.target)
        _assert_untouched(mine)
    finally:
        async with SessionLocal() as db:
            user = await db.get(User, accounts.target)
            user.username = original
            await db.commit()


@pytest.mark.asyncio
async def test_deleting_yourself_is_refused_and_keeps_your_files(accounts, roots):
    profile_root, game_root = roots
    mine = _populate(accounts.admin, profile_root, game_root)
    async with api(accounts.admin, accounts.admin_name) as client:
        response = await client.delete(f"/api/auth/users/{accounts.admin}")
    assert response.status_code == 400
    assert await _user_exists(accounts.admin)
    _assert_untouched(mine)


@pytest.mark.asyncio
async def test_an_unknown_id_without_files_is_still_not_found(accounts, roots):
    async with api(accounts.admin, accounts.admin_name) as client:
        response = await client.delete(f"/api/auth/users/{uuid4()}")
    assert response.status_code == 404


def _symlink_or_skip(link, target) -> None:
    try:
        os.symlink(target, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("this platform cannot create symbolic links without elevated rights")


def test_a_symlink_at_the_owned_name_is_unlinked_not_followed(roots):
    profile_root, game_root = roots
    victim, doomed = uuid4(), uuid4()
    theirs = _populate(victim, profile_root, game_root)
    _symlink_or_skip(game_root / str(doomed), game_root / str(victim))

    result = account_storage.cleanup_account_storage_sync(doomed)

    assert result.complete
    assert not os.path.lexists(game_root / str(doomed))
    _assert_untouched(theirs)


def test_a_symlink_inside_the_tree_is_removed_without_following_it(roots):
    profile_root, game_root = roots
    victim, doomed = uuid4(), uuid4()
    theirs = _populate(victim, profile_root, game_root)
    _populate(doomed, profile_root, game_root)
    _symlink_or_skip(game_root / str(doomed) / "games" / "escape", game_root / str(victim))

    result = account_storage.cleanup_account_storage_sync(doomed)

    assert result.complete
    assert not (game_root / str(doomed)).exists()
    _assert_untouched(theirs)


def test_a_symlinked_root_is_never_followed(roots, tmp_path):
    profile_root, game_root = roots
    doomed = uuid4()
    real = tmp_path / "elsewhere"
    real.mkdir()
    kept = _populate(doomed, profile_root, real)
    game_root.rmdir()
    _symlink_or_skip(game_root, real)

    result = account_storage.cleanup_account_storage_sync(doomed)

    assert not result.complete and "games" in result.failed
    _assert_untouched({path: data for path, data in kept.items() if str(real) in path})


def test_only_a_parsed_uuid_can_name_a_directory(roots):
    _, game_root = roots
    with pytest.raises(ValueError):
        UUID("../other")
    with pytest.raises(ValueError):
        account_storage._owned_path(game_root, "../other")  # type: ignore[arg-type]
    assert account_storage._owned_path(game_root, uuid4()).parent == game_root


def test_cleanup_is_idempotent(roots):
    profile_root, game_root = roots
    user = uuid4()
    _populate(user, profile_root, game_root)
    first = account_storage.cleanup_account_storage_sync(user)
    second = account_storage.cleanup_account_storage_sync(user)
    assert sorted(first.removed) == ["games", "profile"] and first.complete
    assert second.removed == [] and sorted(second.absent) == ["games", "profile"]


def test_the_writers_and_the_cleanup_use_the_same_default_roots():
    """Writers hold their own root constants; cleanup would silently miss data if one drifted."""
    from pathlib import Path

    from src.api.routes import media
    from src.api.routes.plugin_manager import contributions
    from src.api.routes.utils import games as game_utils
    from src.features.trash import sweep
    from src.plugin_api import gateway

    game_writers = [game_utils._DATA_ROOT, contributions._DOCUMENT_DATA_ROOT, gateway._DATA_ROOT]
    profile_writers = [media._USER_DATA_ROOT, sweep._USER_DATA_ROOT]
    assert all(root == Path("/data/users") for root in game_writers)
    assert all(root == Path("/data/user") for root in profile_writers)
