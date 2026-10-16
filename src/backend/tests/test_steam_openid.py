"""Steam OpenID links a verified Steam identity once, for the signed-in account only."""

import time
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import httpx
import pytest
import requests
from sqlalchemy import delete

from src.core.auth import get_current_user
from src.core.crypto import encrypt_secret
from src.database.models.game import Game
from src.database.models.user import User
from src.database.session import SessionLocal
from src.features.imports import steam_openid
from src.main import app

STEAM_ID = "76561198000000001"
OTHER_ID = "76561198000000002"
RETURN_TO = "http://test/api/settings/steam/openid/callback?state=abc"


def assertion(return_to=RETURN_TO, steam_id=STEAM_ID, **changes):
    claimed = f"https://steamcommunity.com/openid/id/{steam_id}"
    params = {
        "openid.ns": "http://specs.openid.net/auth/2.0",
        "openid.mode": "id_res",
        "openid.op_endpoint": steam_openid.STEAM_OPENID_URL,
        "openid.claimed_id": claimed,
        "openid.identity": claimed,
        "openid.return_to": return_to,
        "openid.response_nonce": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()) + "abc",
        "openid.assoc_handle": "1234567890",
        "openid.signed": "signed,op_endpoint,claimed_id,identity,return_to,response_nonce,"
        "assoc_handle",
        "openid.sig": "signature",
    }
    params.update(changes)
    return params


def steam_says(monkeypatch, valid=True):
    post = Mock(
        return_value=SimpleNamespace(
            status_code=200,
            text=f"ns:http://specs.openid.net/auth/2.0\nis_valid:{str(valid).lower()}\n",
        )
    )
    monkeypatch.setattr(steam_openid.requests, "post", post)
    return post


def test_verified_assertion_returns_steam_id_after_steam_confirms(monkeypatch):
    post = steam_says(monkeypatch)
    assert steam_openid.verify(assertion(), RETURN_TO) == STEAM_ID
    sent = post.call_args.kwargs["data"]
    assert sent["openid.mode"] == "check_authentication"
    assert sent["openid.sig"] == "signature"


@pytest.mark.parametrize(
    "changes",
    [
        {"openid.return_to": "http://evil.test/api/settings/steam/openid/callback?state=abc"},
        {"openid.op_endpoint": "https://evil.test/openid/login"},
        {"openid.claimed_id": "https://evil.test/openid/id/76561198000000001"},
        {"openid.identity": "https://steamcommunity.com/openid/id/76561198000000002"},
        {"openid.signed": "op_endpoint,claimed_id,identity"},
        {"openid.response_nonce": "2001-01-01T00:00:00Zold"},
        {"openid.response_nonce": "not-a-time"},
        {"openid.mode": "setup_needed"},
    ],
)
def test_tampered_or_stale_assertions_never_reach_steam(monkeypatch, changes):
    post = steam_says(monkeypatch)
    with pytest.raises(steam_openid.SteamLibraryError):
        steam_openid.verify(assertion(**changes), RETURN_TO)
    post.assert_not_called()


def test_steam_rejection_cancellation_and_network_errors(monkeypatch):
    steam_says(monkeypatch, valid=False)
    with pytest.raises(steam_openid.SteamLibraryError, match="did not confirm"):
        steam_openid.verify(assertion(), RETURN_TO)
    with pytest.raises(steam_openid.SteamOpenIdCancelled):
        steam_openid.verify({"openid.mode": "cancel"}, RETURN_TO)
    monkeypatch.setattr(
        steam_openid.requests, "post", Mock(side_effect=requests.ConnectionError("down"))
    )
    with pytest.raises(steam_openid.SteamLibraryError, match="Could not reach Steam"):
        steam_openid.verify(assertion(), RETURN_TO)


@pytest.fixture
async def account():
    async with SessionLocal() as db:
        user = User(
            username=f"openid_{uuid4().hex[:10]}",
            email=f"{uuid4().hex[:10]}@example.test",
            password_hash="x",
        )
        db.add(user)
        await db.commit()
        identity = user.id

    async def current_actor():
        async with SessionLocal() as db:
            return await db.get(User, identity)

    app.dependency_overrides[get_current_user] = current_actor
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield client, identity
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        async with SessionLocal() as db:
            await db.execute(delete(User).where(User.id == identity))
            await db.commit()


async def sign_in_on_steam(client):
    """Follow the start redirect and return the return_to Steam would send back to."""
    start = await client.get("/api/settings/steam/openid/start")
    assert start.status_code == 303
    location = urlsplit(start.headers["location"])
    assert f"{location.scheme}://{location.netloc}{location.path}" == (
        steam_openid.STEAM_OPENID_URL
    )
    query = parse_qs(location.query)
    assert query["openid.realm"] == ["http://test/"]
    return query["openid.return_to"][0]


def callback_path(return_to, **changes):
    return "/api/settings/steam/openid/callback", assertion(return_to=return_to, **changes)


async def test_round_trip_links_the_verified_profile_once(account, monkeypatch):
    client, identity = account
    steam_says(monkeypatch)
    return_to = await sign_in_on_steam(client)
    path, params = callback_path(return_to)
    done = await client.get(path, params=params)
    assert done.status_code == 303
    assert done.headers["location"].endswith("section=connections&steam_openid=verified")
    async with SessionLocal() as db:
        user = await db.get(User, identity)
        assert user.steam_id == STEAM_ID
        assert user.steam_library_owner_id == STEAM_ID
    # The attempt is one-time: replaying Steam's answer changes nothing.
    async with SessionLocal() as db:
        (await db.get(User, identity)).steam_id = None
        await db.commit()
    replay = await client.get(path, params=params)
    assert replay.headers["location"].endswith("steam_openid=expired")
    async with SessionLocal() as db:
        assert (await db.get(User, identity)).steam_id is None


async def test_callback_without_a_started_attempt_is_refused(account, monkeypatch):
    client, identity = account
    post = steam_says(monkeypatch)
    path, params = callback_path(RETURN_TO)
    done = await client.get(path, params=params)
    assert done.headers["location"].endswith("steam_openid=expired")
    post.assert_not_called()
    async with SessionLocal() as db:
        assert (await db.get(User, identity)).steam_id is None


async def test_answer_for_another_return_url_is_refused(account, monkeypatch):
    client, identity = account
    post = steam_says(monkeypatch)
    await sign_in_on_steam(client)
    path, params = callback_path("http://test/api/settings/steam/openid/callback?state=other")
    done = await client.get(path, params=params)
    assert done.headers["location"].endswith("steam_openid=failed")
    post.assert_not_called()
    async with SessionLocal() as db:
        assert (await db.get(User, identity)).steam_id is None


async def test_cancelled_sign_in_keeps_the_saved_profile(account, monkeypatch):
    client, identity = account
    steam_says(monkeypatch)
    await sign_in_on_steam(client)
    done = await client.get("/api/settings/steam/openid/callback", params={"openid.mode": "cancel"})
    assert done.headers["location"].endswith("steam_openid=cancelled")


@pytest.mark.parametrize("conflict", ["signed-in", "other-owner"])
async def test_existing_connections_are_never_replaced(account, monkeypatch, conflict):
    client, identity = account
    steam_says(monkeypatch)
    async with SessionLocal() as db:
        user = await db.get(User, identity)
        if conflict == "signed-in":
            user.steam_id = OTHER_ID
            user.steam_refresh_token = encrypt_secret("refresh")
        else:
            user.steam_library_owner_id = OTHER_ID
            db.add(
                Game(
                    user_id=identity,
                    title="Imported",
                    sort_title="imported",
                    folder_location="imported",
                    source="Steam",
                    external_id="10",
                )
            )
        await db.commit()
    return_to = await sign_in_on_steam(client)
    path, params = callback_path(return_to)
    done = await client.get(path, params=params)
    assert done.headers["location"].endswith("steam_openid=conflict")
    async with SessionLocal() as db:
        user = await db.get(User, identity)
        assert user.steam_id == (OTHER_ID if conflict == "signed-in" else None)
