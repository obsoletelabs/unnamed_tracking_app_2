"""Sign-in lifecycle isolation, secret redaction and backwards-compatible imports."""

from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import delete

from src.api.routes import library_sync, steam_import_steps
from src.api.routes import settings as settings_routes
from src.core.auth import get_current_user
from src.core.crypto import decrypt_secret
from src.database.models.game import Game, GameStatus
from src.database.models.user import User
from src.database.session import SessionLocal
from src.features.imports import steam_connection as connection
from src.features.metadata.games.steam_session import SteamSession
from src.main import app

OWNER = "76561198000000001"
OTHER_OWNER = "76561198000000002"


@pytest.fixture
async def actors(monkeypatch):
    async with SessionLocal() as db:
        users = [
            User(
                username=f"connection_{uuid4().hex}",
                email=f"{uuid4()}@example.test",
                password_hash="x",
            )
            for _ in range(2)
        ]
        db.add_all(users)
        await db.commit()
        identities = [user.id for user in users]
    clock = SimpleNamespace(now=2_000_000_000)
    monkeypatch.setattr(connection, "time", SimpleNamespace(time=lambda: clock.now))
    start = {
        "client_id": "123",
        "request_id": "cHJpdmF0ZQ==",
        "interval": 3,
        "allowed_confirmations": [{"confirmation_type": 3}],
        "challenge_url": "https://s.team/q/fixture",
        "weak_token": "must-not-persist",
    }
    monkeypatch.setattr(connection, "_begin_provider", Mock(return_value=start))
    try:
        yield identities, clock
    finally:
        async with SessionLocal() as db:
            await db.execute(delete(User).where(User.id.in_(identities)))
            await db.commit()


async def start_attempt(identity, clock):
    async with SessionLocal() as db:
        status = await connection.begin(db, identity)
    clock.now += 4
    return status["pending"]["attempt_id"]


async def connect_account(identity, clock, monkeypatch):
    attempt = await start_attempt(identity, clock)
    session = SteamSession(OWNER, clock.now + 3600, "private-access")
    monkeypatch.setattr(
        connection,
        "_poll_provider",
        Mock(return_value=({"refresh_token": "private-refresh"}, session)),
    )
    async with SessionLocal() as db:
        status = await connection.poll(db, identity, attempt)
    assert status["connected"] and status["method"] == "account"
    return attempt


async def test_pending_is_encrypted_and_scoped_to_actor(actors):
    identities, clock = actors
    attempt = await start_attempt(identities[0], clock)
    async with SessionLocal() as db:
        user = await db.get(User, identities[0])
        assert "client_id" not in user.steam_login_state
        saved = decrypt_secret(user.steam_login_state)
        assert "request_id" in saved and "must-not-persist" not in saved
        public = str(connection.connection_status(user))
        assert "request_id" not in public and "cHJpdmF0ZQ==" not in public
        with pytest.raises(connection.SteamConnectionError, match="cancelled"):
            await connection.poll(db, identities[1], attempt)
    async with SessionLocal() as db:
        await connection.cancel(db, identities[1], attempt)
        assert (await db.get(User, identities[0])).steam_login_state is not None


async def test_account_password_and_guard_are_never_persisted(actors, monkeypatch):
    identities, clock = actors
    result = {
        "client_id": "123",
        "request_id": "cHJpdmF0ZQ==",
        "interval": 3,
        "allowed_confirmations": [{"confirmation_type": 2}],
        "steamid": OWNER,
    }
    begin = Mock(return_value=result)
    guard = Mock()
    monkeypatch.setattr(connection, "_begin_provider", begin)
    monkeypatch.setattr(connection, "_guard_provider", guard)
    async with SessionLocal() as db:
        status = await connection.begin(db, identities[0], "account-name", "private-password")
        begin.assert_called_once_with("account-name", "private-password")
        clock.now += 4
        status = await connection.submit_guard(
            db, identities[0], status["pending"]["attempt_id"], "ABCDE", 2
        )
        stored = decrypt_secret((await db.get(User, identities[0])).steam_login_state)
        assert "private-password" not in stored and "ABCDE" not in stored
        assert status["pending"]["confirmation_types"] == [2]
        assert guard.call_args.args[1:] == ("ABCDE", 2)


@pytest.mark.parametrize("action", ["cancel", "disconnect"])
async def test_late_success_cannot_reconnect_after_cancellation(actors, monkeypatch, action):
    identities, clock = actors
    attempt = await start_attempt(identities[0], clock)

    async def delayed(_function, *_args):
        async with SessionLocal() as other_db:
            if action == "cancel":
                await connection.cancel(other_db, identities[0], attempt)
            else:
                await connection.disconnect(other_db, identities[0])
        return {"refresh_token": "private-refresh"}, SteamSession(
            OWNER, clock.now + 3600, "private-access"
        )

    monkeypatch.setattr(connection, "asyncio", SimpleNamespace(to_thread=delayed))
    async with SessionLocal() as db:
        with pytest.raises(connection.SteamConnectionError, match="cancelled"):
            await connection.poll(db, identities[0], attempt)
    async with SessionLocal() as db:
        user = await db.get(User, identities[0])
        assert user.steam_access_token is None and user.steam_refresh_token is None


async def test_expired_attempt_and_rapid_poll_do_not_reach_steam(actors, monkeypatch):
    identities, clock = actors
    attempt = await start_attempt(identities[0], clock)
    provider = Mock(return_value=({}, None))
    monkeypatch.setattr(connection, "_poll_provider", provider)
    async with SessionLocal() as db:
        await connection.poll(db, identities[0], attempt)
        with pytest.raises(connection.SteamConnectionError) as error:
            await connection.poll(db, identities[0], attempt)
        assert error.value.status_code == 429
    clock.now += 301
    async with SessionLocal() as db:
        with pytest.raises(connection.SteamConnectionError, match="expired"):
            await connection.poll(db, identities[0], attempt)
    provider.assert_called_once()


async def test_disconnect_preserves_library_and_blocks_other_account_progress(actors, monkeypatch):
    identities, clock = actors
    await connect_account(identities[0], clock, monkeypatch)
    async with SessionLocal() as db:
        game = Game(
            user_id=identities[0],
            source="Steam",
            external_id="1230410",
            title="Chaos fixture",
            sort_title="chaos fixture",
            folder_location="chaos-fixture",
            status=GameStatus.PLAYING,
            playtime_seconds=900,
        )
        db.add(game)
        await db.commit()
        game_id = game.id
        await connection.disconnect(db, identities[0])
        assert (await db.get(Game, game_id)).playtime_seconds == 900
        assert (await db.get(User, identities[0])).steam_library_owner_id == OWNER
    attempt = await start_attempt(identities[0], clock)
    monkeypatch.setattr(
        connection,
        "_poll_provider",
        Mock(
            return_value=(
                {"refresh_token": "different-refresh"},
                SteamSession(OTHER_OWNER, clock.now + 3600, "different-access"),
            )
        ),
    )
    async with SessionLocal() as db:
        with pytest.raises(connection.SteamConnectionError, match="another account"):
            await connection.poll(db, identities[0], attempt)
    async with SessionLocal() as db:
        user = await db.get(User, identities[0])
        assert user.steam_refresh_token is None
        assert (await db.get(Game, game_id)).playtime_seconds == 900


async def test_session_renewal_and_late_import_recheck(actors, monkeypatch):
    identities, clock = actors
    await connect_account(identities[0], clock, monkeypatch)
    renew = Mock(return_value=SteamSession(OWNER, clock.now + 3600, "renewed-access"))
    monkeypatch.setattr(connection.steam_login, "exchange_refresh_token", renew)
    clock.now += 3570
    async with SessionLocal() as db:
        user = await db.get(User, identities[0])
        steam_id, credential = await connection.credentials(db, user)
        renew.assert_called_once_with("private-refresh")
        assert credential.access_token == "renewed-access"
        assert decrypt_secret(user.steam_access_token) == "renewed-access"
    async with SessionLocal() as db:
        await connection.disconnect(db, identities[0])
    async with SessionLocal() as db:
        with pytest.raises(connection.SteamConnectionError, match="changed"):
            await connection.recheck(db, identities[0], steam_id, credential)


async def test_renewal_cannot_persist_after_disconnect(actors, monkeypatch):
    identities, clock = actors
    await connect_account(identities[0], clock, monkeypatch)
    clock.now += 3570

    async def delayed(_function, *_args):
        async with SessionLocal() as other_db:
            await connection.disconnect(other_db, identities[0])
        return SteamSession(OWNER, clock.now + 3600, "renewed-access")

    monkeypatch.setattr(connection, "asyncio", SimpleNamespace(to_thread=delayed))
    async with SessionLocal() as db:
        with pytest.raises(connection.SteamConnectionError, match="changed"):
            await connection.credentials(db, await db.get(User, identities[0]))
    async with SessionLocal() as db:
        assert (await db.get(User, identities[0])).steam_access_token is None


@pytest.mark.parametrize("phase", ["bound", "resolve", "validate"])
async def test_late_legacy_save_cannot_restore_disconnected_credentials(actors, monkeypatch, phase):
    identities, _clock = actors
    resolver = Mock(return_value=OWNER)
    validator = Mock(return_value={"validated": True, "persona_name": "Stale result"})
    monkeypatch.setattr(settings_routes.steam, "resolve_steam_id", resolver)
    monkeypatch.setattr(settings_routes, "_validate_provider", validator)

    async def delayed(function, *args):
        if function is (validator if phase == "validate" else resolver):
            async with SessionLocal() as other_db:
                await connection.disconnect(other_db, identities[0])
        return function(*args)

    monkeypatch.setattr(settings_routes, "asyncio", SimpleNamespace(to_thread=delayed))
    async with SessionLocal() as db:
        user = await db.get(User, identities[0])
        user.steam_id, user.steam_api_key = OWNER, "a" * 32
        user.steam_library_owner_id = OWNER if phase == "bound" else None
        await db.commit()
        with pytest.raises(HTTPException) as error:
            await settings_routes._save_steam_credentials(
                db, user, {"steam_id": "profile", "api_key": "a" * 32}
            )
        assert error.value.status_code == 409
    async with SessionLocal() as db:
        user = await db.get(User, identities[0])
        assert user.steam_id is None and user.steam_api_key is None
        assert user.steam_persona_name is None


async def test_legacy_save_reloads_actor_before_overwriting_a_new_owner_session(
    actors, monkeypatch
):
    identities, clock = actors
    monkeypatch.setattr(settings_routes.steam, "resolve_steam_id", Mock(return_value=OWNER))
    monkeypatch.setattr(
        settings_routes, "_validate_provider", Mock(return_value={"validated": True})
    )
    async with SessionLocal() as db:
        stale_user = await db.get(User, identities[0])
        await connect_account(identities[0], clock, monkeypatch)
        with pytest.raises(HTTPException) as error:
            await settings_routes._save_steam_credentials(
                db, stale_user, {"steam_id": OWNER, "api_key": "a" * 32}
            )
        assert error.value.status_code == 409
    async with SessionLocal() as db:
        user = await db.get(User, identities[0])
        assert user.steam_api_key is None
        assert decrypt_secret(user.steam_refresh_token) == "private-refresh"


async def test_steam_validation_errors_do_not_echo_secrets(actors):
    identities, _clock = actors

    async def current_actor():
        async with SessionLocal() as db:
            return await db.get(User, identities[0])

    app.dependency_overrides[get_current_user] = current_actor
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            for body in [
                {"account_name": 123, "password": "private-password"},
                {
                    "account_name": "name",
                    "password": "private-password",
                    "unexpected": "private-code",
                },
            ]:
                reply = await client.post("/api/settings/steam/login/account", json=body)
                assert reply.status_code == 400
                assert "private-" not in reply.text
                assert reply.headers["cache-control"] == "no-store"
    finally:
        app.dependency_overrides.pop(get_current_user, None)


async def test_guard_route_accepts_steams_code_confirmation_types(actors, monkeypatch):
    identities, _clock = actors
    submitted = []

    async def submit(_db, _user_id, _attempt, code, code_type):
        submitted.append((code, code_type))
        return {
            "connected": False,
            "method": None,
            "display_name": None,
            "expires_at": None,
            "pending": None,
        }

    async def current_actor():
        async with SessionLocal() as db:
            return await db.get(User, identities[0])

    monkeypatch.setattr(connection, "submit_guard", submit)
    app.dependency_overrides[get_current_user] = current_actor
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            # Steam's EmailCode (2) and DeviceCode (3); 1 is "no Guard".
            for code_type, expected in [(2, 200), (3, 200), (1, 400), (4, 400)]:
                reply = await client.post(
                    "/api/settings/steam/login/guard",
                    json={"attempt_id": "attempt", "code": "ABCDE", "code_type": code_type},
                )
                assert reply.status_code == expected, reply.text
    finally:
        app.dependency_overrides.pop(get_current_user, None)
    assert submitted == [("ABCDE", 2), ("ABCDE", 3)]


async def test_owner_bulk_and_batched_achievement_imports_need_no_api_key(
    actors, monkeypatch, tmp_path
):
    identities, clock = actors
    await connect_account(identities[0], clock, monkeypatch)
    monkeypatch.setattr(
        library_sync.steam,
        "get_owned_games",
        Mock(return_value=[{"appid": 1230410, "name": "Chaos fixture", "playtime_forever": 15}]),
    )
    from src.helpers import save_game_asset

    monkeypatch.setattr(save_game_asset, "DATA_ROOT", tmp_path)
    async with SessionLocal() as db:
        user = await db.get(User, identities[0])
        result = await library_sync.sync_steam_library(
            db=db, current_user=user, achievements="later"
        )
        assert result["games_added"] == 1
        assert user.steam_api_key is None
        game_id = result["achievement_game_ids"][0]

    async def achievement_data(_owner, credential, _app_id):
        assert isinstance(credential, SteamSession)
        return (
            {"FIRST": {"displayName": "First"}},
            [{"apiname": "FIRST", "achieved": 1, "unlocktime": 100}],
            None,
        )

    monkeypatch.setattr(steam_import_steps, "fetch_steam_achievements", achievement_data)
    async with SessionLocal() as db:
        result = await steam_import_steps.import_achievements(
            steam_import_steps.AchievementsRequest(game_ids=[game_id]),
            db=db,
            current_user=await db.get(User, identities[0]),
        )
        assert result["achievements_synced"] == 1
        assert result["achievements_unavailable"] == []
