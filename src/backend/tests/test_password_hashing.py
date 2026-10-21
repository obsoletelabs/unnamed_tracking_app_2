"""Argon2id hashing policy and transparent upgrade of legacy scrypt records (#289)."""

import asyncio
import hashlib
import secrets
import time

import pytest

from src.api.routes import auth
from src.core.auth import (
    hash_password,
    password_needs_rehash,
    session_cookie_name,
    verify_password,
)
from src.core.config import settings
from src.database.models.user import User
from src.database.session import SessionLocal
from tests.test_account_security import NEW_PASSWORD, PASSWORD
from tests.test_account_security import accounts as accounts  # noqa: F401


def legacy_hash(password: str, n: int = 2**14) -> str:
    """The format written before Argon2id, kept readable so existing accounts keep working."""
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=n, r=8, p=1, dklen=32)
    return f"scrypt${n}$8$1${salt.hex()}${digest.hex()}"


async def stored_hash(user_id) -> str:
    async with SessionLocal() as db:
        user = await db.get(User, user_id)
        await db.refresh(user)
        return user.password_hash


async def force_hash(user_id, value: str) -> None:
    async with SessionLocal() as db:
        user = await db.get(User, user_id)
        user.password_hash = value
        await db.commit()


def login(accounts, password=PASSWORD, index=0):
    return accounts.client.post(
        "/api/auth/login",
        json={"username_or_email": accounts.users[index].username, "password": password},
    )


def test_new_hashes_use_the_configured_argon2id_policy():
    stored = hash_password("Correct!9")
    algorithm, memory, time_cost, lanes, salt, digest = stored.split("$")
    assert algorithm == "argon2id"
    assert (int(memory), int(time_cost), int(lanes)) == (
        settings.ARGON2_MEMORY_KIB,
        settings.ARGON2_TIME_COST,
        settings.ARGON2_PARALLELISM,
    )
    assert len(bytes.fromhex(salt)) == 16 and len(bytes.fromhex(digest)) == 32
    assert "Correct!9" not in stored
    assert stored != hash_password("Correct!9")
    assert verify_password("Correct!9", stored)
    assert not verify_password("Correct!8", stored)
    assert not password_needs_rehash(stored)


def test_legacy_scrypt_records_still_verify_and_are_marked_for_upgrade():
    stored = legacy_hash("Correct!9")
    assert verify_password("Correct!9", stored)
    assert not verify_password("Wrong!9", stored)
    assert password_needs_rehash(stored)


def test_a_changed_policy_marks_existing_argon2_hashes_for_upgrade(monkeypatch):
    stored = hash_password("Correct!9")
    monkeypatch.setattr(settings, "ARGON2_TIME_COST", settings.ARGON2_TIME_COST + 1)
    assert verify_password("Correct!9", stored)
    assert password_needs_rehash(stored)


@pytest.mark.parametrize(
    "stored",
    [
        "",
        "not-a-valid-hash",
        "bcrypt$1$2$3$aa$bb",
        "argon2id$19456$2$1$zz$yy",
        "argon2id$19456$2$1$" + "aa" * 16,
        "argon2id$0$2$1$" + "aa" * 16 + "$" + "bb" * 32,
        "argon2id$19456$2$1$" + "aa" * 16 + "$" + "bb" * 8,
        "argon2id$4294967296$2$1$" + "aa" * 16 + "$" + "bb" * 32,
        "argon2id$19456$999999$1$" + "aa" * 16 + "$" + "bb" * 32,
        "scrypt$3$8$1$" + "aa" * 16 + "$" + "bb" * 32,
        "scrypt$1073741824$8$1$" + "aa" * 16 + "$" + "bb" * 32,
        "scrypt$16384$8$1$" + "aa" * 16 + "$",
    ],
)
def test_corrupt_or_unbounded_records_are_rejected_quickly(stored):
    started = time.monotonic()
    assert not verify_password("Correct!9", stored)
    assert time.monotonic() - started < 2


async def test_scrypt_account_signs_in_and_is_upgraded_exactly_once(accounts):
    user_id = accounts.users[0].id
    await force_hash(user_id, legacy_hash(PASSWORD))
    assert (await login(accounts)).status_code == 200
    upgraded = await stored_hash(user_id)
    assert upgraded.startswith("argon2id$") and verify_password(PASSWORD, upgraded)
    assert PASSWORD not in upgraded
    # An up-to-date record is left alone, so sign-ins do not keep rewriting the row.
    assert (await login(accounts)).status_code == 200
    assert await stored_hash(user_id) == upgraded


async def test_wrong_password_never_upgrades_or_changes_the_record(accounts):
    user_id = accounts.users[0].id
    original = legacy_hash(PASSWORD)
    await force_hash(user_id, original)
    assert (await login(accounts, "Wrong-Password-1!")).status_code == 401
    assert await stored_hash(user_id) == original


async def test_busy_hashing_pool_postpones_the_upgrade_but_not_the_sign_in(accounts, monkeypatch):
    user_id = accounts.users[0].id
    original = legacy_hash(PASSWORD)
    await force_hash(user_id, original)
    real = auth.run_password_job

    async def busy_for_hashing(job, *args):
        if job is hash_password:
            raise auth.PasswordWorkBusy
        return await real(job, *args)

    monkeypatch.setattr(auth, "run_password_job", busy_for_hashing)
    assert (await login(accounts)).status_code == 200
    assert await stored_hash(user_id) == original


async def test_concurrent_password_change_is_never_overwritten_by_the_upgrade(accounts):
    user_id = accounts.users[0].id
    await force_hash(user_id, legacy_hash(PASSWORD))
    accounts.client.cookies.set(session_cookie_name("test"), accounts.tokens[0])
    results = await asyncio.gather(
        login(accounts),
        accounts.client.patch(
            "/api/auth/me", json={"current_password": PASSWORD, "new_password": NEW_PASSWORD}
        ),
    )
    assert results[1].status_code == 200
    final = await stored_hash(user_id)
    assert final.startswith("argon2id$")
    assert verify_password(NEW_PASSWORD, final)
    assert not verify_password(PASSWORD, final)


async def test_password_change_writes_argon2id_and_inactive_accounts_are_not_upgraded(accounts):
    user_id = accounts.users[0].id
    await force_hash(user_id, legacy_hash(PASSWORD))
    accounts.client.cookies.set(session_cookie_name("test"), accounts.tokens[0])
    response = await accounts.client.patch(
        "/api/auth/me", json={"current_password": PASSWORD, "new_password": NEW_PASSWORD}
    )
    assert response.status_code == 200
    assert (await stored_hash(user_id)).startswith("argon2id$")
    async with SessionLocal() as db:
        user = await db.get(User, user_id)
        user.is_active = False
        user.password_hash = legacy_hash(NEW_PASSWORD)
        await db.commit()
    assert (await login(accounts, NEW_PASSWORD)).status_code == 401
    assert (await stored_hash(user_id)).startswith("scrypt$")


def test_synthetic_unusable_passwords_are_argon2id_and_never_verify_user_input():
    synthetic = hash_password(secrets.token_urlsafe(48) + "A!a")
    assert synthetic.startswith("argon2id$")
    assert not verify_password("", synthetic)
    assert not verify_password("Guess-Password-1!", synthetic)
