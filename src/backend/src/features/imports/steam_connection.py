"""Actor-scoped Steam sign-in, cancellation and encrypted session renewal.

No database lock is held across a provider request. Every delayed result is
rechecked against the current attempt or credentials before it can be applied.
Passwords and Guard codes are transient inputs, never part of saved state.
"""

import asyncio
import json
import time
from dataclasses import replace
from uuid import UUID, uuid4

import requests
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.crypto import decrypt_secret, encrypt_secret
from src.database.models.game import Game
from src.database.models.user import User
from src.features.metadata.games import steam, steam_login

_ATTEMPT_SECONDS = 300
_RENEW_BEFORE_SECONDS = 60


class SteamConnectionError(steam.SteamLibraryError):
    """A safe local validation/conflict message, rather than a provider failure."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


async def locked_user(db: AsyncSession, user_id: UUID) -> User:
    user = await db.scalar(
        select(User)
        .where(User.id == user_id, User.is_active.is_(True))
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if user is None:
        raise SteamConnectionError("This app account is no longer active.", 401)
    return user


# Every Fernet token starts with its version byte, which encodes to this prefix.
_FERNET_PREFIX = "gAAAAA"


def stored_api_key(user: User) -> str | None:
    """The legacy Web API key; rows saved before encryption still hold it in plain text."""
    value = user.steam_api_key
    if not value:
        return None
    return decrypt_secret(value) if value.startswith(_FERNET_PREFIX) else value


def store_api_key(user: User, key: str | None) -> None:
    user.steam_api_key = encrypt_secret(key) if key else None


def _pending(user: User) -> dict | None:
    if not user.steam_login_state:
        return None
    try:
        result = json.loads(decrypt_secret(user.steam_login_state))
        if not isinstance(result, dict) or result["expires_at"] <= time.time():
            return None
        return result
    except (ValueError, KeyError, TypeError, RuntimeError):
        raise SteamConnectionError("Steam sign-in expired. Start a new sign-in.") from None


def _save_pending(user: User, state: dict) -> None:
    user.steam_login_state = encrypt_secret(json.dumps(state))


def connection_status(user: User) -> dict:
    """Never return tokens, provider request IDs, passwords or Guard codes."""
    pending = _pending(user)
    has_session = bool(user.steam_refresh_token and user.steam_access_token and user.steam_id)
    legacy = bool(user.steam_id and user.steam_api_key)
    public_pending = None
    if pending:
        public_pending = {
            "attempt_id": pending["attempt_id"],
            "expires_at": pending["expires_at"],
            "interval": pending["interval"],
            "challenge_url": pending.get("challenge_url"),
            "confirmation_types": [
                item["confirmation_type"] for item in pending["allowed_confirmations"]
            ],
            "phase": pending["phase"],
        }
    return {
        "connected": has_session or legacy,
        "method": "account" if has_session else "api_key" if legacy else None,
        "display_name": user.steam_persona_name if has_session or legacy else None,
        "expires_at": user.steam_token_expires_at if has_session else None,
        "pending": public_pending,
    }


def _begin_provider(account_name: str | None, password: str | None) -> dict:
    with requests.Session() as client:
        if account_name is None:
            return steam_login.begin_qr(client)
        return steam_login.begin_credentials(client, account_name, password or "")


async def begin(
    db: AsyncSession, user_id: UUID, account_name: str | None = None, password: str | None = None
) -> dict:
    user = await locked_user(db, user_id)
    old = _pending(user)
    if old and old["next_action_at"] > time.time():
        raise SteamConnectionError("Wait a few seconds before starting another sign-in.", 429)
    attempt = uuid4().hex
    state: dict = {
        "attempt_id": attempt,
        "expires_at": int(time.time()) + _ATTEMPT_SECONDS,
        "next_action_at": time.time() + 10,
        "interval": 3,
        "phase": "starting",
        "allowed_confirmations": [],
    }
    _save_pending(user, state)
    await db.commit()
    try:
        result = await asyncio.to_thread(_begin_provider, account_name, password)
    except steam.SteamLibraryError:
        await cancel(db, user_id, attempt)
        raise
    user = await locked_user(db, user_id)
    _require_attempt(user, attempt)
    # Persist only protocol fields used by subsequent requests. Passwords,
    # weak tokens and other opaque response fields never enter the database.
    state.update(
        {
            key: result[key]
            for key in ("client_id", "request_id", "interval", "allowed_confirmations")
        }
    )
    state.update({key: result[key] for key in ("challenge_url", "steamid") if key in result})
    state.update(phase="waiting", next_action_at=time.time() + state["interval"])
    _save_pending(user, state)
    await db.commit()
    return connection_status(user)


def _require_attempt(user: User, attempt: str) -> dict:
    state = _pending(user)
    if state is None or state["attempt_id"] != attempt:
        raise SteamConnectionError("This Steam sign-in expired or was cancelled.", 409)
    return state


async def cancel(db: AsyncSession, user_id: UUID, attempt: str) -> dict:
    user = await locked_user(db, user_id)
    state = _pending(user)
    if state and state["attempt_id"] == attempt:
        user.steam_login_state = None
    await db.commit()
    return connection_status(user)


async def disconnect(db: AsyncSession, user_id: UUID) -> dict:
    user = await locked_user(db, user_id)
    if not user.steam_library_owner_id and user.steam_id and len(user.steam_id) == 17:
        user.steam_library_owner_id = user.steam_id
    for field in (
        "steam_id",
        "steam_api_key",
        "steam_access_token",
        "steam_refresh_token",
        "steam_token_expires_at",
        "steam_login_state",
        "steam_persona_name",
        "steam_avatar_url",
    ):
        setattr(user, field, None)
    await db.commit()
    return connection_status(user)


async def _claim(db: AsyncSession, user_id: UUID, attempt: str, *, paced: bool = True) -> dict:
    """Serialize provider calls; only automatic polls wait for Steam's interval.

    A typed Guard code is a one-off user action. Pacing it like a poll would
    reject most submissions, because the client polls whenever the interval ends.
    """
    user = await locked_user(db, user_id)
    state = _require_attempt(user, attempt)
    if state["phase"] != "waiting" or (paced and state["next_action_at"] > time.time()):
        raise SteamConnectionError("Wait for the current Steam sign-in request.", 429)
    state["next_action_at"] = time.time() + max(30, state["interval"])
    state["phase"] = "checking"
    _save_pending(user, state)
    await db.commit()
    return state


def _poll_provider(state: dict) -> tuple[dict, steam.SteamSession | None]:
    with requests.Session() as client:
        result = steam_login.poll(client, state)
    refresh = result.get("refresh_token")
    session = steam_login.exchange_refresh_token(refresh) if refresh else None
    return result, session


async def check_library_owner(db: AsyncSession, user: User, steam_id: str) -> None:
    """The current core library supports one Steam owner; retain that boundary."""
    owner = user.steam_library_owner_id or user.steam_id
    if owner and owner != steam_id:
        existing = await db.scalar(
            select(Game.id).where(Game.user_id == user.id, Game.source == "Steam").limit(1)
        )
        if existing is not None:
            raise SteamConnectionError(
                "These Steam imports belong to another account. Reconnect the original account "
                "to preserve its progress. Changing accounts requires permanently removing "
                "those Steam imports, including any in Trash.",
                409,
            )
    user.steam_library_owner_id = steam_id


async def poll(db: AsyncSession, user_id: UUID, attempt: str) -> dict:
    state = await _claim(db, user_id, attempt)
    try:
        result, session = await asyncio.to_thread(_poll_provider, state)
    except steam.SteamLibraryError:
        await cancel(db, user_id, attempt)
        raise
    user = await locked_user(db, user_id)
    _require_attempt(user, attempt)
    if session is not None:
        try:
            await check_library_owner(db, user, session.steam_id)
        except SteamConnectionError:
            # The sign-in is finished; its session is discarded, so end the attempt.
            user.steam_login_state = None
            await db.commit()
            raise
        user.steam_id = session.steam_id
        user.steam_api_key = None
        user.steam_access_token = encrypt_secret(session.access_token)
        user.steam_refresh_token = encrypt_secret(result["refresh_token"])
        user.steam_token_expires_at = session.expires_at
        user.steam_login_state = None
        # Steam account names are not necessarily public persona names.
        user.steam_persona_name = None
        user.steam_avatar_url = None
    else:
        if result.get("new_client_id"):
            value = result["new_client_id"]
            if (
                not isinstance(value, str)
                or not value.isascii()
                or not value.isdigit()
                or not 0 < int(value) < 2**64
            ):
                raise steam.SteamLibraryError("Steam returned an invalid sign-in response.")
            state["client_id"] = value
        if result.get("new_challenge_url"):
            state["challenge_url"] = result["new_challenge_url"]
        state["next_action_at"] = time.time() + state["interval"]
        state["phase"] = "waiting"
        _save_pending(user, state)
    await db.commit()
    return connection_status(user)


def _guard_provider(state: dict, code: str, code_type: int) -> None:
    with requests.Session() as client:
        steam_login.submit_guard(client, state, code, code_type)


async def submit_guard(
    db: AsyncSession, user_id: UUID, attempt: str, code: str, code_type: int
) -> dict:
    state = await _claim(db, user_id, attempt, paced=False)
    try:
        await asyncio.to_thread(_guard_provider, state, code, code_type)
    finally:
        user = await locked_user(db, user_id)
        _require_attempt(user, attempt)
        state["next_action_at"] = time.time() + state["interval"]
        state["phase"] = "waiting"
        _save_pending(user, state)
        await db.commit()
    return connection_status(user)


async def credentials(db: AsyncSession, user: User) -> tuple[str, str | steam.SteamSession]:
    """Renew expiring browser sessions, retaining the existing API-key alternative."""
    user = await locked_user(db, user.id)
    steam_id, refresh = user.steam_id, user.steam_refresh_token
    if not steam_id:
        raise SteamConnectionError("Connect Steam in Settings > Connections first.")
    if refresh and user.steam_access_token:
        if (user.steam_token_expires_at or 0) > time.time() + _RENEW_BEFORE_SECONDS:
            session = steam.SteamSession(
                steam_id, user.steam_token_expires_at or 0, decrypt_secret(user.steam_access_token)
            )
        else:
            await db.commit()
            session = await asyncio.to_thread(
                steam_login.exchange_refresh_token, decrypt_secret(refresh)
            )
            user = await locked_user(db, user.id)
            if user.steam_refresh_token != refresh or user.steam_id != steam_id:
                raise SteamConnectionError("Steam connection changed during this request.", 409)
            if session.steam_id != steam_id:
                raise steam.SteamLibraryError(
                    "Steam could not verify this account. Reconnect Steam."
                )
            user.steam_access_token = encrypt_secret(session.access_token)
            user.steam_token_expires_at = session.expires_at
        await check_library_owner(db, user, steam_id)
        await db.commit()
        return steam_id, replace(session, connection_id=refresh)
    key = stored_api_key(user)
    if not key:
        raise SteamConnectionError("Reconnect Steam in Settings > Connections.")
    if user.steam_api_key == key:
        # Encrypt a key saved before keys were encrypted, now that it is in use.
        store_api_key(user, key)
    await db.commit()
    previous_id = steam_id
    steam_id = await asyncio.to_thread(steam.resolve_steam_id, steam_id, key)
    user = await locked_user(db, user.id)
    if stored_api_key(user) != key or user.steam_id != previous_id:
        raise SteamConnectionError("Steam connection changed during this request.", 409)
    await check_library_owner(db, user, steam_id)
    user.steam_id = steam_id
    await db.commit()
    return steam_id, key


async def recheck(
    db: AsyncSession, user_id: UUID, steam_id: str, credential: str | steam.SteamSession
) -> None:
    """Call before saving provider snapshots, and hold the lock until commit."""
    user = await locked_user(db, user_id)
    if isinstance(credential, steam.SteamSession):
        matches = bool(
            user.steam_refresh_token
            and user.steam_id == steam_id
            and user.steam_refresh_token == credential.connection_id
        )
    else:
        matches = stored_api_key(user) == credential and user.steam_id == steam_id
    if not matches:
        raise SteamConnectionError(
            "Steam connection changed during this import. Saved progress was kept.", 409
        )
