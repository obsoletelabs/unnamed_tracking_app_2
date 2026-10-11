"""Account-owner Steam sessions; validation and private achievement reads.

Steam OpenID supplies identity only. A Steam Store web API session authorizes
owner reads. Bind its token to the owner returned by Steam's authenticated
browser-session exchange, never to unverified JWT claims alone.
"""

import base64
import binascii
import json
import re
import time
from dataclasses import dataclass, field

import requests

WEB_API_BASE = "https://api.steampowered.com"
SESSION_PAGE = "https://store.steampowered.com/pointssummary/ajaxgetasyncconfig"
_RECONNECT = "Your Steam session has expired or was rejected. Reconnect Steam in Connections."
_INVALID_SESSION = "Steam returned an invalid session. Reconnect Steam in Connections."


class SteamLibraryError(RuntimeError):
    """Steam rejected a library or achievement read."""


@dataclass(frozen=True)
class SteamSession:
    steam_id: str
    expires_at: int
    access_token: str = field(repr=False)
    connection_id: str | None = field(default=None, repr=False)

    def check_expiry(self) -> None:
        if self.expires_at <= int(time.time()):
            raise SteamLibraryError(_RECONNECT)


def credential_params(credentials: str | SteamSession) -> dict[str, str]:
    if isinstance(credentials, SteamSession):
        credentials.check_expiry()
        return {"access_token": credentials.access_token}
    return {"key": credentials}


def parse_token(value: str) -> SteamSession:
    """Claims are provisional until bound to Steam's authenticated browser owner."""
    if len(value) > 16384:
        raise SteamLibraryError(_INVALID_SESSION)
    token = value.strip()
    try:
        if token.startswith("{"):
            body = json.loads(token)
            token = body["data"]["webapi_token"]
        if not isinstance(token, str) or not 0 < len(token) <= 8192:
            raise ValueError
        parts = token.split(".")
        if len(parts) != 3 or not all(parts):
            raise ValueError
        payload = base64.b64decode(
            parts[1] + "=" * (-len(parts[1]) % 4), altchars=b"-_", validate=True
        )
        claims = json.loads(payload)
        steam_id, expiry = claims["sub"], claims["exp"]
        if not isinstance(steam_id, str) or not steam_id.isascii() or not steam_id.isdigit():
            raise ValueError
        if (
            len(steam_id) != 17
            or not isinstance(expiry, int)
            or isinstance(expiry, bool)
            or not 0 < expiry < 2**63
        ):
            raise ValueError
    except (ValueError, TypeError, KeyError, binascii.Error, UnicodeError):
        raise SteamLibraryError(_INVALID_SESSION) from None
    provisional = SteamSession(steam_id, expiry, token)
    provisional.check_expiry()
    return provisional


def _read(
    client: requests.Session,
    session: SteamSession,
    method: str,
    params: dict[str, str],
) -> dict:
    """Fixed Steam endpoints only; never echo token-bearing URLs or responses."""
    try:
        response = client.get(
            f"{WEB_API_BASE}/{method}",
            params={**credential_params(session), **params},
            timeout=20,
            allow_redirects=False,
        )
    except requests.RequestException:
        raise SteamLibraryError("Could not reach Steam. Try again shortly.") from None
    if response.status_code in (401, 403):
        raise SteamLibraryError(_RECONNECT)
    if response.status_code >= 400:
        raise SteamLibraryError(
            f"Steam request failed ({response.status_code}). Try again shortly."
        )
    result = response.headers.get("x-eresult")
    if result in {"5", "15", "25", "27"}:
        raise SteamLibraryError(_RECONNECT)
    if result is not None and result != "1":
        raise SteamLibraryError("Steam could not provide this account's data. Try again shortly.")
    try:
        body = response.json()
    except ValueError:
        raise SteamLibraryError("Steam returned invalid JSON.") from None
    if not isinstance(body, dict):
        raise SteamLibraryError("Steam returned an invalid response.")
    if "success" in body and body["success"] not in (1, True):
        raise SteamLibraryError(_RECONNECT)
    payload = body.get("response", body)
    if not isinstance(payload, dict):
        raise SteamLibraryError("Steam returned an invalid response.")
    return payload


def validate_session(value: str, verified_steam_id: str) -> SteamSession:
    """Bind a Store-issued token to the authenticated finalizelogin identity.

    The caller must obtain both values directly from Steam over HTTPS, using
    its own browser cookie jar. The legacy OAuth GetTokenDetails endpoint does
    not accept Store API JWTs and cannot validate this browser-token audience.
    """
    try:
        body = json.loads(value)
        if (
            not isinstance(body, dict)
            or not isinstance(body.get("data"), dict)
            or not isinstance(body["data"].get("webapi_token"), str)
        ):
            raise ValueError
        success = body.get("success")
        if not isinstance(success, int) or isinstance(success, bool) or success != 1:
            raise ValueError
    except (ValueError, TypeError):
        raise SteamLibraryError(_INVALID_SESSION) from None
    provisional = parse_token(body["data"]["webapi_token"])
    if verified_steam_id != provisional.steam_id:
        raise SteamLibraryError("Steam could not verify the session's account. Reconnect Steam.")
    return provisional


def _achievement_icon(value: object, app_id: int) -> str | None:
    """Owner schemas contain CDN filenames rather than legacy full URLs."""
    if not isinstance(value, str) or not re.fullmatch(r"[a-fA-F0-9]{40}\.(?:jpg|jpeg|png)", value):
        return None
    return f"https://shared.fastly.steamstatic.com/community_assets/images/apps/{app_id}/{value}"


def _achievement_schema(entries: object, app_id: int) -> tuple[dict[str, dict], dict[int, str]]:
    if not isinstance(entries, list):
        raise SteamLibraryError("Steam did not provide an achievement schema.")
    schema: dict[str, dict] = {}
    names: dict[int, str] = {}
    lower_names: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise SteamLibraryError("Steam returned an invalid achievement schema.")
        key, name = entry.get("internal_key"), entry.get("internal_name")
        if (
            not isinstance(key, int)
            or isinstance(key, bool)
            or key < 0
            or not isinstance(name, str)
            or not name
        ):
            raise SteamLibraryError("Steam returned an invalid achievement schema.")
        if key in names or name.lower() in lower_names:
            raise SteamLibraryError("Steam returned an invalid achievement schema.")
        names[key] = name
        lower_names.add(name.lower())
        schema[name] = {
            "displayName": entry.get("localized_name") or name,
            "description": entry.get("localized_desc"),
            "icon": _achievement_icon(entry.get("icon"), app_id),
            "icongray": _achievement_icon(entry.get("icon_gray"), app_id),
            "hidden": entry.get("hidden", False),
        }
    return schema, names


def _player_achievements(
    player_entries: object, names: dict[int, str], *, allow_sparse: bool = False
) -> list[dict]:
    if not isinstance(player_entries, list):
        raise SteamLibraryError("Steam did not provide this account's achievement progress.")
    unlocked: list[dict] = []
    seen: set[int] = set()
    for entry in player_entries:
        if not isinstance(entry, dict):
            raise SteamLibraryError("Steam returned invalid achievement progress.")
        key = entry.get("internal_key")
        if not isinstance(key, int) or isinstance(key, bool) or key not in names or key in seen:
            raise SteamLibraryError("Steam returned invalid achievement progress.")
        achieved, when = entry.get("unlocked", False), entry.get("unlock_time", 0)
        if (
            not isinstance(achieved, bool)
            or not isinstance(when, int)
            or isinstance(when, bool)
            or not 0 <= when <= 0xFFFFFFFF
        ):
            raise SteamLibraryError("Steam returned invalid achievement progress.")
        seen.add(key)
        unlocked.append({"apiname": names[key], "achieved": int(achieved), "unlocktime": when})
    if seen != set(names) and not allow_sparse:
        raise SteamLibraryError(
            "Steam returned incomplete achievement progress. Stored progress was kept."
        )
    if allow_sparse:
        # Steam omits untouched protobuf rows. A matching schema hash permits
        # expansion to the complete existing import contract, with locked defaults.
        unlocked.extend(
            {"apiname": name, "achieved": 0, "unlocktime": 0}
            for key, name in names.items()
            if key not in seen
        )
    return unlocked


def get_achievement_data(
    client: requests.Session, session: SteamSession, app_id: int
) -> tuple[dict[str, dict], list[dict]]:
    """Normalize owner API rows to the existing Steam achievement contract.

    Match internal keys and schema versions. Steam's compact progress omits
    untouched rows; expand those only with a verified matching schema hash.
    Unverified or mismatched progress must not overwrite the saved snapshot.
    """
    definitions = _read(
        client,
        session,
        "IPlayerService/GetGameAchievements/v1/",
        {"appid": str(app_id), "language": "english"},
    )
    schema, names = _achievement_schema(definitions.get("achievements"), app_id)
    if not schema:
        return {}, []
    progress = _read(
        client,
        session,
        "IPlayerService/GetUserAchievements/v1/",
        {"appid": str(app_id), "steamid": session.steam_id},
    )
    verified_schema = False
    for field_name in ("schema_hash", "schema_version"):
        if field_name in definitions or field_name in progress:
            values = (definitions.get(field_name), progress.get(field_name))
            if (
                any(
                    not isinstance(value, int)
                    or isinstance(value, bool)
                    or not 0 <= value <= 0xFFFFFFFF
                    for value in values
                )
                or values[0] != values[1]
            ):
                raise SteamLibraryError("Steam's achievement schema changed. Try again shortly.")
            if field_name == "schema_hash" and values[0] != 0:
                verified_schema = True
    entries = progress.get("achievements", [] if verified_schema else None)
    return schema, _player_achievements(entries, names, allow_sparse=verified_schema)
