"""Steam OpenID 2.0 sign-in: a redirect that proves which SteamID a person controls.

Steam offers OpenID 2.0, not an OAuth authorization-code flow (OAuth needs a
client issued by Valve). OpenID only proves identity. It never yields a Steam
session or permission to read a private library or achievements, so a verified
SteamID is the same public-data link as a typed profile ID, except that it can
no longer be someone else's. Passwords are entered on steamcommunity.com only.
"""

import asyncio
import hashlib
import hmac
import re
import secrets
import time
from datetime import datetime, timezone
from urllib.parse import urlencode
from uuid import UUID

import requests
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models.user import User
from src.features.imports.steam_connection import (
    SteamConnectionError,
    check_library_owner,
    locked_user,
)

OPENID_ENDPOINT = "https://steamcommunity.com/openid/login"
CALLBACK_PATH = "/api/settings/steam/openid/callback"
_NS = "http://specs.openid.net/auth/2.0"
_IDENTIFIER_SELECT = "http://specs.openid.net/auth/2.0/identifier_select"
_CLAIMED_ID = re.compile(r"^https?://steamcommunity\.com/openid/id/(\d{17})$")
_STATE_SECONDS = 600
_NONCE_SKEW_SECONDS = 300
_MAX_PARAMS = 40
_MAX_VALUE = 2048
# Fields Steam must have signed for the assertion to bind identity, target and freshness.
_REQUIRED_SIGNED = {
    "op_endpoint",
    "claimed_id",
    "identity",
    "return_to",
    "response_nonce",
    "assoc_handle",
}


def _digest(state: str) -> str:
    return hashlib.sha256(state.encode()).hexdigest()


def _return_to(origin: str, state: str) -> str:
    return f"{origin}{CALLBACK_PATH}?{urlencode({'state': state})}"


def authorization_url(origin: str, state: str) -> str:
    """Steam's sign-in page URL. It carries no secrets: the state only ties the return."""
    query = {
        "openid.ns": _NS,
        "openid.mode": "checkid_setup",
        "openid.return_to": _return_to(origin, state),
        "openid.realm": origin,
        "openid.identity": _IDENTIFIER_SELECT,
        "openid.claimed_id": _IDENTIFIER_SELECT,
    }
    return f"{OPENID_ENDPOINT}?{urlencode(query)}"


async def begin(db: AsyncSession, user_id: UUID, origin: str) -> str:
    user = await locked_user(db, user_id)
    state = secrets.token_urlsafe(32)
    # Only a digest is stored; a new redirect replaces (and so cancels) an older one.
    user.steam_openid_state = f"{_digest(state)}|{int(time.time()) + _STATE_SECONDS}|{origin}"
    await db.commit()
    return authorization_url(origin, state)


# The only reasons the callback ever reports back to the browser. The Settings page maps each to
# fixed wording, so the redirect URL can never carry text that gets displayed.
ERROR_CODES = frozenset(
    {
        "expired",
        "invalid",
        "cancelled",
        "too_old",
        "unreachable",
        "unverified",
        "different_account",
        "failed",
    }
)


def error_code(exc: SteamConnectionError) -> str:
    """The redirect code for a failure; anything unclassified is the generic ``failed``."""
    return exc.code if exc.code in ERROR_CODES else "failed"


def _take_state(user: User, state: str) -> str:
    """Consume the one-time state, returning the origin it was issued for."""
    stored, user.steam_openid_state = user.steam_openid_state, None
    try:
        digest, expires, origin = (stored or "").split("|", 2)
        valid = hmac.compare_digest(digest, _digest(state)) and int(expires) > time.time()
    except ValueError:
        valid = False
    if not valid:
        raise SteamConnectionError(
            "This Steam sign-in expired or was already used.", 409, "expired"
        )
    return origin


def _verify_assertion(params: dict[str, str], return_to: str) -> str:
    """Local checks on the positive assertion; returns the claimed SteamID64."""
    invalid = SteamConnectionError("Steam returned an invalid sign-in response.", 400, "invalid")
    if params.get("openid.mode") == "cancel":
        raise SteamConnectionError("Steam sign-in was cancelled.", 400, "cancelled")
    if len(params) > _MAX_PARAMS or any(len(value) > _MAX_VALUE for value in params.values()):
        raise invalid
    expected = {
        "openid.ns": _NS,
        "openid.mode": "id_res",
        "openid.op_endpoint": OPENID_ENDPOINT,
        "openid.return_to": return_to,
    }
    if any(params.get(key) != value for key, value in expected.items()):
        raise invalid
    claimed = _CLAIMED_ID.match(params.get("openid.claimed_id", ""))
    if claimed is None or params.get("openid.identity") != params["openid.claimed_id"]:
        raise invalid
    signed = set(params.get("openid.signed", "").split(","))
    if not _REQUIRED_SIGNED <= signed or not params.get("openid.sig"):
        raise invalid
    try:
        issued = datetime.strptime(
            params.get("openid.response_nonce", "")[:20], "%Y-%m-%dT%H:%M:%SZ"
        ).replace(tzinfo=timezone.utc)
    except ValueError:
        raise invalid from None
    if abs(time.time() - issued.timestamp()) > _NONCE_SKEW_SECONDS:
        raise SteamConnectionError("This Steam sign-in response is too old.", 400, "too_old")
    return claimed.group(1)


def _check_authentication(params: dict[str, str]) -> bool:
    """Ask Steam to confirm it issued this exact assertion (also invalidates its nonce)."""
    body = {key: value for key, value in params.items() if key.startswith("openid.")}
    body["openid.mode"] = "check_authentication"
    try:
        response = requests.post(
            OPENID_ENDPOINT, data=body, timeout=15, allow_redirects=False, stream=True
        )
        content = response.raw.read(4096, decode_content=True)
        response.close()
    except requests.RequestException:
        raise SteamConnectionError(
            "Could not reach Steam. Try again shortly.", 502, "unreachable"
        ) from None
    if response.status_code != 200:
        return False
    fields = dict(
        line.split(":", 1)
        for line in content.decode("utf-8", "replace").splitlines()
        if ":" in line
    )
    return fields.get("is_valid") == "true"


async def complete(db: AsyncSession, user_id: UUID, query: dict[str, str]) -> str:
    """Validate Steam's callback and link the verified SteamID to this app account."""
    user = await locked_user(db, user_id)
    try:
        origin = _take_state(user, query.get("state", ""))
    finally:
        # Used or not, the state can never be presented again.
        await db.commit()
    params = {key: value for key, value in query.items() if key.startswith("openid.")}
    steam_id = _verify_assertion(params, _return_to(origin, query["state"]))
    if not await asyncio.to_thread(_check_authentication, params):
        raise SteamConnectionError("Steam could not verify this sign-in.", 400, "unverified")
    user = await locked_user(db, user_id)
    if user.steam_refresh_token and user.steam_id != steam_id:
        raise SteamConnectionError(
            "A different Steam account is signed in. Disconnect it before linking another.",
            409,
            "different_account",
        )
    await check_library_owner(db, user, steam_id)
    if user.steam_id != steam_id:
        user.steam_persona_name = user.steam_avatar_url = None
    user.steam_id = user.steam_verified_id = steam_id
    await db.commit()
    return steam_id
