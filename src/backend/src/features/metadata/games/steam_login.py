"""Steam's browser authentication protocol, shared by QR and account sign-in.

Passwords and Guard codes are used once and never belong in persisted state.
Only Steam's browser platform is requested; credentials never reach arbitrary
transfer URLs or the application's shared metadata HTTP session cookie jar.
"""

import base64
import json
import math
import secrets
from urllib.parse import urlsplit

import requests
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from src.features.metadata.games import steam_session

_HEADERS = {"Origin": "https://steamcommunity.com", "Referer": "https://steamcommunity.com/"}
_DEVICE = {"device_friendly_name": "Unnamed Tracking App", "platform_type": 2}
_TRANSFER_URLS = {
    "https://store.steampowered.com/login/settoken",
    "https://store.steampowered.com/login/settoken/",
}


def _request(client: requests.Session, method: str, values: dict, *, get: bool = False) -> dict:
    url = f"{steam_session.WEB_API_BASE}/IAuthenticationService/{method}/v1/"
    try:
        if get:
            response = client.get(
                url, params=values, headers=_HEADERS, timeout=20, allow_redirects=False
            )
        else:
            response = client.post(
                url,
                data={"input_json": json.dumps(values)},
                headers=_HEADERS,
                timeout=20,
                allow_redirects=False,
            )
    except requests.RequestException:
        raise steam_session.SteamLibraryError("Could not reach Steam. Try again shortly.") from None
    result = response.headers.get("x-eresult")
    if result == "5":
        raise steam_session.SteamLibraryError("Steam did not accept the account name or password.")
    if result == "84" or response.status_code == 429:
        raise steam_session.SteamLibraryError(
            "Steam is limiting sign-in requests. Try again later."
        )
    if response.status_code != 200 or result != "1":
        raise steam_session.SteamLibraryError(
            "Steam did not accept this sign-in attempt. Try again."
        )
    try:
        body = response.json()
    except ValueError:
        raise steam_session.SteamLibraryError(
            "Steam returned an invalid sign-in response."
        ) from None
    if not isinstance(body, dict) or not isinstance(body.get("response"), dict):
        raise steam_session.SteamLibraryError("Steam returned an invalid sign-in response.")
    return body["response"]


def validate_challenge_url(value: object) -> str:
    if not isinstance(value, str) or not 0 < len(value) <= 512:
        raise steam_session.SteamLibraryError("Steam returned an invalid QR challenge.")
    try:
        url = urlsplit(value)
    except ValueError:
        raise steam_session.SteamLibraryError("Steam returned an invalid QR challenge.") from None
    if (
        url.scheme != "https"
        or url.netloc != "s.team"
        or not url.path.startswith("/q/")
        or url.query
        or url.fragment
    ):
        raise steam_session.SteamLibraryError("Steam returned an invalid QR challenge.")
    return value


def _validate_start(payload: dict) -> dict:
    client_id, request_id = payload.get("client_id"), payload.get("request_id")
    interval = payload.get("interval")
    if (
        not isinstance(client_id, str)
        or not client_id.isascii()
        or not client_id.isdigit()
        or len(client_id) > 20
        or not 0 < int(client_id) < 2**64
    ):
        raise steam_session.SteamLibraryError("Steam returned an incomplete sign-in response.")
    if not isinstance(request_id, str) or not 0 < len(request_id) <= 512:
        raise steam_session.SteamLibraryError("Steam returned an incomplete sign-in response.")
    if (
        isinstance(interval, bool)
        or not isinstance(interval, (int, float))
        or not math.isfinite(interval)
        or not 0 < interval <= 30
    ):
        raise steam_session.SteamLibraryError("Steam returned an incomplete sign-in response.")
    try:
        decoded_id = base64.b64decode(request_id, validate=True)
    except ValueError:
        raise steam_session.SteamLibraryError(
            "Steam returned an invalid sign-in response."
        ) from None
    if not decoded_id:
        raise steam_session.SteamLibraryError("Steam returned an incomplete sign-in response.")
    confirmations = payload.get("allowed_confirmations")
    if not isinstance(confirmations, list) or any(
        not isinstance(item, dict) or item.get("confirmation_type") not in {0, 1, 2, 3, 4, 5, 6}
        for item in confirmations
    ):
        raise steam_session.SteamLibraryError("Steam returned an unsupported confirmation method.")
    return payload


def begin_qr(client: requests.Session) -> dict:
    result = _validate_start(_request(client, "BeginAuthSessionViaQR", {"device_details": _DEVICE}))
    validate_challenge_url(result.get("challenge_url"))
    return result


def begin_credentials(client: requests.Session, account_name: str, password: str) -> dict:
    if not account_name.strip() or len(account_name) > 100 or not 0 < len(password) <= 1024:
        raise steam_session.SteamLibraryError("Enter your Steam account name and password.")
    account_name = account_name.strip()
    public_key = _request(
        client, "GetPasswordRSAPublicKey", {"account_name": account_name}, get=True
    )
    try:
        modulus = int(public_key["publickey_mod"], 16)
        exponent = int(public_key["publickey_exp"], 16)
        if not 1024 <= modulus.bit_length() <= 4096 or exponent != 65537:
            raise ValueError
        timestamp = str(public_key["timestamp"])
        if not timestamp.isascii() or not timestamp.isdigit():
            raise ValueError
        key = rsa.RSAPublicNumbers(exponent, modulus).public_key()
        encrypted = base64.b64encode(key.encrypt(password.encode(), padding.PKCS1v15())).decode()
    except (KeyError, ValueError, TypeError):
        raise steam_session.SteamLibraryError(
            "Steam could not prepare a secure account sign-in."
        ) from None
    result = _validate_start(
        _request(
            client,
            "BeginAuthSessionViaCredentials",
            {
                "account_name": account_name,
                "encrypted_password": encrypted,
                "encryption_timestamp": timestamp,
                "remember_login": True,
                "persistence": 1,
                "website_id": "Community",
                "device_details": _DEVICE,
            },
        )
    )
    steam_id = result.get("steamid")
    if (
        not isinstance(steam_id, str)
        or not steam_id.isascii()
        or not steam_id.isdigit()
        or len(steam_id) != 17
    ):
        raise steam_session.SteamLibraryError("Steam returned an incomplete account identity.")
    return result


def poll(client: requests.Session, pending: dict) -> dict:
    result = _request(
        client,
        "PollAuthSessionStatus",
        {
            "client_id": pending["client_id"],
            "request_id": pending["request_id"],
        },
    )
    if result.get("agreement_session_url"):
        raise steam_session.SteamLibraryError(
            "Sign in to Steam directly to review its account agreement, then retry."
        )
    if result.get("new_challenge_url"):
        validate_challenge_url(result["new_challenge_url"])
    return result


def submit_guard(client: requests.Session, pending: dict, code: str, code_type: int) -> None:
    allowed = {item["confirmation_type"] for item in pending["allowed_confirmations"]}
    if code_type not in {1, 2} or code_type not in allowed or not pending.get("steamid"):
        raise steam_session.SteamLibraryError("This sign-in does not accept that Steam Guard code.")
    if not 0 < len(code) <= 10 or not code.isascii() or not code.isalnum():
        raise steam_session.SteamLibraryError("Enter the Steam Guard code shown by Steam.")
    _request(
        client,
        "UpdateAuthSessionWithSteamGuardCode",
        {
            "client_id": pending["client_id"],
            "steamid": pending["steamid"],
            "code": code,
            "code_type": code_type,
        },
    )


def exchange_refresh_token(refresh_token: str) -> steam_session.SteamSession:
    """Get a Store API token using Steam's normal browser-session exchange."""
    identity = steam_session.parse_token(refresh_token)
    # A private cookie jar per exchange prevents one user's cookies reaching another.
    with requests.Session() as client:
        try:
            response = client.post(
                "https://login.steampowered.com/jwt/finalizelogin",
                data={
                    "nonce": refresh_token,
                    "sessionid": secrets.token_hex(12),
                    "redir": "https://steamcommunity.com/login/home/?goto=",
                },
                headers=_HEADERS,
                timeout=20,
                allow_redirects=False,
            )
            body = response.json()
            transfers = body.get("transfer_info") if isinstance(body, dict) else None
            if response.status_code != 200 or not isinstance(transfers, list) or len(transfers) > 8:
                raise steam_session.SteamLibraryError(
                    "Steam could not renew this connection. Reconnect Steam."
                )
            transfer = next(
                (
                    entry
                    for entry in transfers
                    if isinstance(entry, dict) and entry.get("url") in _TRANSFER_URLS
                ),
                None,
            )
            if transfer is None or not isinstance(transfer.get("params"), dict):
                raise steam_session.SteamLibraryError(
                    "Steam returned an invalid browser-session exchange."
                )
            params = transfer["params"]
            if set(params) != {"nonce", "auth"} or any(
                not isinstance(value, str) or not 0 < len(value) <= 8192
                for value in params.values()
            ):
                raise steam_session.SteamLibraryError(
                    "Steam returned an invalid browser-session exchange."
                )
            result = client.post(
                transfer["url"],
                data={**params, "steamID": identity.steam_id},
                timeout=20,
                allow_redirects=False,
            )
            if result.status_code != 200 or result.json().get("result", 1) != 1:
                raise steam_session.SteamLibraryError(
                    "Steam could not complete the browser-session exchange."
                )
            response = client.get(steam_session.SESSION_PAGE, timeout=20, allow_redirects=False)
            if response.status_code != 200:
                raise steam_session.SteamLibraryError("Steam could not provide a library session.")
            session = steam_session.validate_session(client, response.text)
        except requests.RequestException:
            raise steam_session.SteamLibraryError(
                "Could not reach Steam. Try again shortly."
            ) from None
        except (ValueError, TypeError, KeyError, AttributeError):
            raise steam_session.SteamLibraryError(
                "Steam returned an invalid browser-session exchange."
            ) from None
    if session.steam_id != identity.steam_id:
        raise steam_session.SteamLibraryError(
            "Steam returned a different account. Reconnect Steam."
        )
    return session
