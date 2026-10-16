"""Steam OpenID 2.0: verify which Steam account a person owns, and nothing more.

OpenID proves identity only. It grants no access to private library or achievement
data; a legacy connection still reads public data with the person's own Web API key,
and account sign-in remains the way to import private progress.
"""

import re
import time
from calendar import timegm
from collections.abc import Mapping
from urllib.parse import urlencode

import requests

from src.features.metadata.games.steam import SteamLibraryError

STEAM_OPENID_URL = "https://steamcommunity.com/openid/login"
ATTEMPT_SECONDS = 300
_NS = "http://specs.openid.net/auth/2.0"
_IDENTIFIER_SELECT = "http://specs.openid.net/auth/2.0/identifier_select"
_CLAIMED_ID = re.compile(r"https://steamcommunity\.com/openid/id/(\d{17})")
_SIGNED_FIELDS = {"op_endpoint", "claimed_id", "identity", "return_to", "response_nonce"}
_INVALID = "Steam did not confirm this sign-in. Try again."


class SteamOpenIdCancelled(SteamLibraryError):
    """The person backed out on Steam's sign-in page."""


class SteamOpenIdExpired(SteamLibraryError):
    """No live attempt matches this browser and account."""


def login_url(return_to: str, realm: str) -> str:
    """Steam's sign-in page; Steam sends the person back to the exact return_to."""
    return f"{STEAM_OPENID_URL}?" + urlencode(
        {
            "openid.ns": _NS,
            "openid.mode": "checkid_setup",
            "openid.return_to": return_to,
            "openid.realm": realm,
            "openid.identity": _IDENTIFIER_SELECT,
            "openid.claimed_id": _IDENTIFIER_SELECT,
        }
    )


def _nonce_time(nonce: str) -> int | None:
    try:
        return timegm(time.strptime(nonce[:20], "%Y-%m-%dT%H:%M:%SZ"))
    except ValueError:
        return None


def _vouched_steam_id(params: Mapping[str, str], return_to: str, now: float) -> str | None:
    """The claimed SteamID64 when the response is well formed for this attempt."""
    claimed = params.get("openid.claimed_id", "")
    match = _CLAIMED_ID.fullmatch(claimed)
    issued = _nonce_time(params.get("openid.response_nonce", ""))
    checks = (
        params.get("openid.mode") == "id_res",
        params.get("openid.ns") == _NS,
        params.get("openid.op_endpoint") == STEAM_OPENID_URL,
        params.get("openid.return_to") == return_to,
        params.get("openid.identity") == claimed,
        _SIGNED_FIELDS <= set(params.get("openid.signed", "").split(",")),
        issued is not None and abs(now - issued) <= ATTEMPT_SECONDS,
    )
    return match[1] if match and all(checks) else None


def verify(params: Mapping[str, str], return_to: str, now: float | None = None) -> str:
    """The SteamID64 Steam vouches for in this response, after Steam re-checks its signature."""
    if params.get("openid.mode") == "cancel":
        raise SteamOpenIdCancelled("Steam sign-in was cancelled.")
    steam_id = _vouched_steam_id(params, return_to, time.time() if now is None else now)
    if steam_id is None:
        raise SteamLibraryError(_INVALID)
    body = {key: value for key, value in params.items() if key.startswith("openid.")}
    body["openid.mode"] = "check_authentication"
    try:
        response = requests.post(STEAM_OPENID_URL, data=body, timeout=15, allow_redirects=False)
    except requests.RequestException:
        raise SteamLibraryError("Could not reach Steam. Try again shortly.") from None
    if response.status_code != 200 or "is_valid:true" not in response.text.splitlines():
        raise SteamLibraryError(_INVALID)
    return steam_id
