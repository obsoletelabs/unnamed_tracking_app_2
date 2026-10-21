"""Sign-in timing parity across account states and password redaction (#289 follow-up)."""

import logging
from uuid import uuid4

import httpx
import pytest

from src.core.auth import hash_password, session_cookie_name
from src.features import local_auth
from src.main import app
from tests.test_account_security import PASSWORD
from tests.test_account_security import accounts as accounts  # noqa: F401
from tests.test_password_hashing import force_hash, legacy_hash

CANARY = "Canary-Password-0xC0FFEE-ZZ!"


class NoLegacyDatabase:
    async def scalar(self, _statement):
        return False


class LegacyDatabase:
    async def scalar(self, _statement):
        return True


@pytest.fixture
def algorithms(monkeypatch):
    """The algorithm of every verification a sign-in performs, in order."""
    seen: list[str] = []
    real = local_auth.verify_password

    def recording(password, stored):
        seen.append(stored.split("$")[0])
        return real(password, stored)

    monkeypatch.setattr(local_auth, "verify_password", recording)
    return seen


def cache(monkeypatch, *, remain: bool):
    monkeypatch.setitem(local_auth._legacy_check, "until", 10**12)
    monkeypatch.setitem(local_auth._legacy_check, "remain", remain)


@pytest.mark.parametrize(
    ("stored", "expected"),
    [
        (None, ["argon2id", "scrypt"]),
        (hash_password("Other-Password-1!"), ["argon2id", "scrypt"]),
        (legacy_hash("Other-Password-1!"), ["scrypt", "argon2id"]),
    ],
    ids=["nonexistent", "argon2id-account", "scrypt-account"],
)
async def test_every_attempt_spends_both_algorithms_while_scrypt_accounts_remain(
    monkeypatch, algorithms, stored, expected
):
    cache(monkeypatch, remain=True)
    await local_auth.verify_login_password(LegacyDatabase(), "Any-Password-1!", stored)
    assert algorithms == expected


async def test_attempts_do_one_verification_once_every_account_is_on_argon2id(
    monkeypatch, algorithms
):
    cache(monkeypatch, remain=False)
    await local_auth.verify_login_password(NoLegacyDatabase(), "Any-Password-1!", None)
    await local_auth.verify_login_password(
        NoLegacyDatabase(), "Any-Password-1!", hash_password("Other-Password-1!")
    )
    assert algorithms == ["argon2id", "argon2id"]


async def test_a_stale_cache_cannot_leak_a_late_scrypt_account(monkeypatch, algorithms):
    cache(monkeypatch, remain=False)
    await local_auth.verify_login_password(
        NoLegacyDatabase(), "Any-Password-1!", legacy_hash("Other-Password-1!")
    )
    assert algorithms == ["scrypt", "argon2id"]
    assert local_auth._legacy_check["remain"] is True


async def test_the_scrypt_check_is_refreshed_from_the_database(monkeypatch):
    monkeypatch.setitem(local_auth._legacy_check, "until", 0.0)
    assert await local_auth._legacy_hashes_remain(NoLegacyDatabase(), known_legacy=False) is False
    # Cached: a different answer is not asked for until the interval passes.
    assert await local_auth._legacy_hashes_remain(LegacyDatabase(), known_legacy=False) is False
    monkeypatch.setitem(local_auth._legacy_check, "until", 0.0)
    assert await local_auth._legacy_hashes_remain(LegacyDatabase(), known_legacy=False) is True


async def test_sign_in_results_are_unchanged_by_the_balancing_step(accounts, monkeypatch):  # noqa: F811
    cache(monkeypatch, remain=True)
    user = accounts.users[0]
    await force_hash(user.id, legacy_hash(PASSWORD))
    good = {"username_or_email": user.username, "password": PASSWORD}
    bad = {"username_or_email": user.username, "password": "Wrong-Password-1!"}
    ghost = {"username_or_email": f"ghost-{uuid4().hex}", "password": PASSWORD}
    assert (await accounts.client.post("/api/auth/login", json=good)).status_code == 200
    assert (await accounts.client.post("/api/auth/login", json=bad)).status_code == 401
    assert (await accounts.client.post("/api/auth/login", json=ghost)).status_code == 401


@pytest.fixture
async def real_app():
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def test_submitted_passwords_never_reach_responses_or_logs(
    accounts,
    real_app,
    caplog,
    capfd,  # noqa: F811
):
    """A canary password through every rejection path: bodies, logs, stdout and stderr."""
    caplog.set_level(logging.DEBUG)
    user = accounts.users[0]
    real_app.cookies.set(session_cookie_name("test"), accounts.tokens[0])
    attempts = [
        ("post", "/api/auth/login", {"username_or_email": user.username, "password": CANARY}),
        ("post", "/api/auth/login", {"username_or_email": "nobody", "password": CANARY}),
        ("post", "/api/auth/login", {"username_or_email": user.username, "password": CANARY * 60}),
        ("post", "/api/auth/login", {"username_or_email": 7, "password": CANARY}),
        ("patch", "/api/auth/me", {"current_password": CANARY, "new_password": "Next-Pass-123!"}),
        ("patch", "/api/auth/me", {"current_password": PASSWORD, "new_password": CANARY[:5]}),
        ("patch", "/api/auth/me", {"current_password": CANARY * 60, "new_password": CANARY}),
    ]
    for method, path, body in attempts:
        response = await getattr(real_app, method)(path, json=body)
        assert response.status_code in {400, 401, 422}, (path, response.status_code)
        assert CANARY not in response.text
    output = capfd.readouterr()
    assert CANARY not in caplog.text + output.out + output.err


async def test_an_unexpected_failure_while_verifying_does_not_echo_the_password(
    accounts,
    real_app,
    caplog,
    capfd,
    monkeypatch,  # noqa: F811
):
    def explode(*_args, **_kwargs):
        raise RuntimeError("verification backend unavailable")

    monkeypatch.setattr(local_auth, "verify_password", explode)
    caplog.set_level(logging.DEBUG)
    response = await real_app.post(
        "/api/auth/login",
        json={"username_or_email": accounts.users[0].username, "password": CANARY},
    )
    assert response.status_code == 500
    output = capfd.readouterr()
    assert CANARY not in response.text + caplog.text + output.out + output.err
