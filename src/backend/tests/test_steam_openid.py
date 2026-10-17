"""Steam OpenID redirect sign-in: state, replay, callback validation and account linking."""

import time
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import delete

from src.core.auth import get_current_user
from src.database.models.game import Game, GameStatus
from src.database.models.user import User
from src.database.session import SessionLocal
from src.features.imports import steam_connection, steam_openid
from src.main import app

OWNER = "76561198000000001"
OTHER = "76561198000000002"
ORIGIN = "https://tracker.example.test"


@pytest.fixture
async def actor():
    async with SessionLocal() as db:
        user = User(
            username=f"openid_{uuid4().hex}", email=f"{uuid4()}@example.test", password_hash="x"
        )
        db.add(user)
        await db.commit()
        identity = user.id
    try:
        yield identity
    finally:
        async with SessionLocal() as db:
            await db.execute(delete(Game).where(Game.user_id == identity))
            await db.execute(delete(User).where(User.id == identity))
            await db.commit()


@pytest.fixture
def steam(monkeypatch):
    """Stands in for Steam's check_authentication answer; records what was sent."""
    sent: list[dict] = []
    answer = {"valid": True}

    def fake_check(params: dict) -> bool:
        sent.append(params)
        return answer["valid"]

    monkeypatch.setattr(steam_openid, "_check_authentication", fake_check)
    return sent, answer


async def start(identity) -> str:
    async with SessionLocal() as db:
        url = await steam_openid.begin(db, identity, ORIGIN)
    return parse_qs(urlsplit(url).query)["openid.return_to"][0]


def assertion(return_to: str, steam_id: str = OWNER, **overrides: str) -> dict[str, str]:
    nonce = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()) + "abc123"
    claimed = f"https://steamcommunity.com/openid/id/{steam_id}"
    params = {
        "openid.ns": "http://specs.openid.net/auth/2.0",
        "openid.mode": "id_res",
        "openid.op_endpoint": steam_openid.OPENID_ENDPOINT,
        "openid.claimed_id": claimed,
        "openid.identity": claimed,
        "openid.return_to": return_to,
        "openid.response_nonce": nonce,
        "openid.assoc_handle": "1234567890",
        "openid.signed": "signed,op_endpoint,claimed_id,identity,return_to,"
        "response_nonce,assoc_handle",
        "openid.sig": "c2ln",
        "state": parse_qs(urlsplit(return_to).query)["state"][0],
    }
    return {**params, **overrides}


async def finish(identity, params) -> str:
    async with SessionLocal() as db:
        return await steam_openid.complete(db, identity, params)


async def test_redirect_goes_to_steam_and_stores_only_a_state_digest(actor):
    async with SessionLocal() as db:
        url = await steam_openid.begin(db, actor, ORIGIN)
        parts = urlsplit(url)
        query = parse_qs(parts.query)
        assert f"{parts.scheme}://{parts.netloc}{parts.path}" == steam_openid.OPENID_ENDPOINT
        assert query["openid.mode"] == ["checkid_setup"]
        assert query["openid.realm"] == [ORIGIN]
        assert query["openid.return_to"][0].startswith(f"{ORIGIN}{steam_openid.CALLBACK_PATH}?")
        state = parse_qs(urlsplit(query["openid.return_to"][0]).query)["state"][0]
        stored = (await db.get(User, actor)).steam_openid_state
        assert state not in stored and len(state) >= 40


async def test_valid_callback_links_verified_identity_and_is_single_use(actor, steam):
    sent, _ = steam
    return_to = await start(actor)
    params = assertion(return_to)
    assert await finish(actor, params) == OWNER
    assert sent[0]["openid.claimed_id"].endswith(OWNER)
    async with SessionLocal() as db:
        user = await db.get(User, actor)
        status = steam_connection.connection_status(user)
        assert user.steam_id == OWNER and user.steam_openid_state is None
        # Identity only: no session, no API key, so no library access is implied.
        assert status["connected"] is False and status["verified_steam_id"] == OWNER
        assert not user.steam_refresh_token and not user.steam_api_key
    with pytest.raises(steam_connection.SteamConnectionError, match="already used"):
        await finish(actor, params)
    assert len(sent) == 1


async def test_steam_rejecting_the_assertion_links_nothing(actor, steam):
    _, answer = steam
    answer["valid"] = False
    with pytest.raises(steam_connection.SteamConnectionError, match="could not verify"):
        await finish(actor, assertion(await start(actor)))
    async with SessionLocal() as db:
        user = await db.get(User, actor)
        assert user.steam_id is None and user.steam_verified_id is None
        assert user.steam_openid_state is None


@pytest.mark.parametrize(
    "tamper",
    [
        {"openid.mode": "cancel"},
        {"openid.ns": "http://specs.openid.net/auth/1.1"},
        {"openid.op_endpoint": "https://evil.example.test/openid/login"},
        {"openid.return_to": ORIGIN + "/api/settings/steam/openid/callback?state=other"},
        {"openid.claimed_id": "https://evil.example.test/openid/id/76561198000000001"},
        {"openid.claimed_id": "https://steamcommunity.com/openid/id/765611980000000011"},
        {"openid.identity": "https://steamcommunity.com/openid/id/76561198000000002"},
        {"openid.signed": "signed,op_endpoint,return_to"},
        {"openid.sig": ""},
        {"openid.response_nonce": "2001-01-01T00:00:00Zold"},
        {"openid.response_nonce": "not-a-nonce"},
    ],
)
async def test_malformed_or_tampered_assertions_are_rejected_before_asking_steam(
    actor, steam, tamper
):
    sent, _ = steam
    with pytest.raises(steam_connection.SteamConnectionError):
        await finish(actor, assertion(await start(actor), **tamper))
    assert sent == []
    async with SessionLocal() as db:
        assert (await db.get(User, actor)).steam_id is None


async def test_callback_without_or_with_wrong_state_is_rejected(actor, steam):
    sent, _ = steam
    return_to = await start(actor)
    with pytest.raises(steam_connection.SteamConnectionError, match="expired"):
        await finish(actor, assertion(return_to, state="forged"))
    # The failed attempt consumed the real state as well.
    with pytest.raises(steam_connection.SteamConnectionError, match="expired"):
        await finish(actor, assertion(return_to))
    with pytest.raises(steam_connection.SteamConnectionError, match="expired"):
        await finish(actor, {k: v for k, v in assertion(return_to).items() if k != "state"})
    assert sent == []


async def test_expired_state_is_rejected(actor, steam, monkeypatch):
    return_to = await start(actor)
    real = time.time()
    monkeypatch.setattr(steam_openid.time, "time", lambda: real + 601)
    with pytest.raises(steam_connection.SteamConnectionError, match="expired"):
        await finish(actor, assertion(return_to))


async def test_state_is_bound_to_the_app_account_that_started_it(actor, steam):
    return_to = await start(actor)
    async with SessionLocal() as db:
        other = User(username=f"o_{uuid4().hex}", email=f"{uuid4()}@e.test", password_hash="x")
        db.add(other)
        await db.commit()
        other_id = other.id
    try:
        with pytest.raises(steam_connection.SteamConnectionError, match="expired"):
            await finish(other_id, assertion(return_to))
        # The starter's own pending state is untouched by the stranger's attempt.
        assert await finish(actor, assertion(return_to)) == OWNER
    finally:
        async with SessionLocal() as db:
            await db.execute(delete(User).where(User.id == other_id))
            await db.commit()


async def test_new_redirect_invalidates_the_previous_one(actor, steam):
    first = await start(actor)
    await start(actor)
    with pytest.raises(steam_connection.SteamConnectionError, match="expired"):
        await finish(actor, assertion(first))


async def test_signed_in_account_session_cannot_be_swapped_by_openid(actor, steam):
    async with SessionLocal() as db:
        user = await db.get(User, actor)
        user.steam_id, user.steam_refresh_token = OWNER, "x"
        await db.commit()
    with pytest.raises(steam_connection.SteamConnectionError, match="Disconnect"):
        await finish(actor, assertion(await start(actor), OTHER))


async def test_library_owner_boundary_still_protects_imported_games(actor, steam):
    async with SessionLocal() as db:
        user = await db.get(User, actor)
        user.steam_library_owner_id = OWNER
        db.add(
            Game(
                user_id=actor,
                folder_location="steam/owned",
                title="Owned",
                sort_title="owned",
                source="Steam",
                status=GameStatus.BACKLOG,
            )
        )
        await db.commit()
    with pytest.raises(steam_connection.SteamConnectionError, match="another account"):
        await finish(actor, assertion(await start(actor), OTHER))


async def test_verified_flag_follows_the_current_steam_id_and_disconnect(actor, steam):
    await finish(actor, assertion(await start(actor)))
    async with SessionLocal() as db:
        user = await db.get(User, actor)
        user.steam_id = OTHER  # e.g. a typed legacy profile ID replaced the verified one
        assert steam_connection.connection_status(user)["verified_steam_id"] is None
        user.steam_id = OWNER
        assert steam_connection.connection_status(user)["verified_steam_id"] == OWNER
        status = await steam_connection.disconnect(db, actor)
        assert status["verified_steam_id"] is None
        assert (await db.get(User, actor)).steam_verified_id is None


def test_check_authentication_posts_only_openid_fields_and_needs_is_valid_true(monkeypatch):
    posted = {}

    class Reply:
        def __init__(self, body: bytes, status_code: int = 200):
            self.status_code = status_code
            self.raw = SimpleNamespace(read=lambda *_a, **_k: body)

        def close(self):
            pass

    def post(url, data, **kwargs):
        posted.update(url=url, data=data, kwargs=kwargs)
        return Reply(posted["body"], posted.get("status", 200))

    monkeypatch.setattr(steam_openid.requests, "post", post)
    params = assertion(ORIGIN + "/cb?state=s")
    posted["body"] = b"ns:http://specs.openid.net/auth/2.0\nis_valid:true\n"
    assert steam_openid._check_authentication(params) is True
    assert posted["url"] == steam_openid.OPENID_ENDPOINT
    assert posted["data"]["openid.mode"] == "check_authentication"
    assert "state" not in posted["data"] and posted["kwargs"]["allow_redirects"] is False
    posted["body"] = b"ns:http://specs.openid.net/auth/2.0\nis_valid:false\n"
    assert steam_openid._check_authentication(params) is False
    posted["body"] = b"is_valid:true\n"
    posted["status"] = 500
    assert steam_openid._check_authentication(params) is False


async def test_steam_unreachable_reports_a_safe_error(actor, monkeypatch):
    import requests

    def boom(*_args, **_kwargs):
        raise requests.ConnectionError("secret detail")

    monkeypatch.setattr(steam_openid.requests, "post", boom)
    with pytest.raises(steam_connection.SteamConnectionError, match="Could not reach Steam"):
        await finish(actor, assertion(await start(actor)))


@pytest.fixture
async def client(actor):
    async with SessionLocal() as db:
        user = await db.get(User, actor)
        db.expunge(user)

    async def current() -> User:
        async with SessionLocal() as db:
            return await db.get(User, actor)

    app.dependency_overrides[get_current_user] = current
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=ORIGIN) as http:
        yield http
    app.dependency_overrides.pop(get_current_user, None)


async def test_http_begin_and_callback_round_trip(client, actor, steam):
    response = await client.post("/api/settings/steam/openid/begin")
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    url = response.json()["redirect_url"]
    assert url.startswith(steam_openid.OPENID_ENDPOINT + "?")
    return_to = parse_qs(urlsplit(url).query)["openid.return_to"][0]
    assert return_to.startswith(ORIGIN + steam_openid.CALLBACK_PATH)
    params = assertion(return_to)
    done = await client.get(steam_openid.CALLBACK_PATH, params=params)
    assert done.status_code == 303
    assert done.headers["location"] == "/settings?section=connections&steam_openid=linked"
    assert done.headers["referrer-policy"] == "no-referrer"
    replay = await client.get(steam_openid.CALLBACK_PATH, params=params)
    assert replay.status_code == 303 and "steam_openid=failed" in replay.headers["location"]
    status = (await client.get("/api/settings/steam")).json()
    assert status["verified_steam_id"] == OWNER and status["connected"] is False


def _error_query(location: str) -> dict[str, list[str]]:
    return parse_qs(urlsplit(location).query)


async def test_callback_redirects_with_a_known_code_never_message_text(client, actor, steam):
    await start(actor)
    replay = await client.get(steam_openid.CALLBACK_PATH, params={"state": "forged"})
    query = _error_query(replay.headers["location"])
    assert query["steam_openid"] == ["failed"]
    assert query["steam_openid_error"] == ["expired"]
    assert "expired" not in replay.headers["location"].replace("steam_openid_error=expired", "")


@pytest.mark.parametrize(
    ("raised", "expected"),
    [
        (steam_connection.SteamConnectionError("Visit evil.example now", 400), "failed"),
        (steam_connection.SteamConnectionError("Visit evil.example now", 400, "made_up"), "failed"),
        (steam_connection.SteamConnectionError("x", 400, "cancelled"), "cancelled"),
    ],
)
async def test_only_known_error_codes_are_emitted(client, actor, monkeypatch, raised, expected):
    async def fail(*_args, **_kwargs):
        raise raised

    monkeypatch.setattr(steam_openid, "complete", fail)
    response = await client.get(steam_openid.CALLBACK_PATH)
    assert _error_query(response.headers["location"])["steam_openid_error"] == [expected]
    assert "evil" not in response.headers["location"]
    assert expected in steam_openid.ERROR_CODES
