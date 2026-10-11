"""Steam QR/account authentication never persists or leaks submitted secrets."""

import base64
import json
import time
from unittest.mock import Mock

import pytest
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from src.features.metadata.games import steam_login, steam_session


def reply(payload, *, result="1"):
    response = Mock(status_code=200, headers={"x-eresult": result})
    response.json.return_value = {"response": payload}
    return response


def pending():
    return {
        "client_id": "123456",
        "request_id": base64.b64encode(b"request").decode(),
        "interval": 5,
        "challenge_url": "https://s.team/q/1/123456",
        "allowed_confirmations": [{"confirmation_type": 3}],
        "steamid": "76561198000000001",
    }


def test_qr_requests_browser_platform_and_has_no_account_credentials():
    client = Mock()
    client.post.return_value = reply(pending())
    result = steam_login.begin_qr(client)
    assert result["challenge_url"] == pending()["challenge_url"]
    params = json.loads(client.post.call_args.kwargs["data"]["input_json"])
    assert params == {
        "device_details": {"device_friendly_name": "Unnamed Tracking App", "platform_type": 2}
    }
    assert client.post.call_args.kwargs["allow_redirects"] is False


@pytest.mark.parametrize(
    "url",
    [
        "https://s.team.example.test/q/1/123",
        "http://s.team/q/1/123",
        "https://s.team@other.test/q/1/123",
        "https://s.team/q/1/123?token=secret",
        "https://s.team/other",
    ],
)
def test_only_steam_qr_urls_are_accepted(url):
    client = Mock()
    client.post.return_value = reply({**pending(), "challenge_url": url})
    with pytest.raises(steam_session.SteamLibraryError):
        steam_login.begin_qr(client)


def test_password_is_rsa_encrypted_for_steam_and_not_returned():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    numbers = key.public_key().public_numbers()
    client = Mock()
    client.get.return_value = reply(
        {"publickey_mod": format(numbers.n, "x"), "publickey_exp": "010001", "timestamp": "123456"}
    )
    client.post.return_value = reply(pending())
    result = steam_login.begin_credentials(client, "account-name", "fixture-password")
    values = json.loads(client.post.call_args.kwargs["data"]["input_json"])
    assert "fixture-password" not in json.dumps(values)
    assert "fixture-password" not in json.dumps(result)
    assert (
        key.decrypt(base64.b64decode(values["encrypted_password"]), padding.PKCS1v15())
        == b"fixture-password"
    )
    assert values["device_details"]["platform_type"] == 2


def test_guard_code_must_match_steams_allowed_confirmation():
    client = Mock()
    with pytest.raises(steam_session.SteamLibraryError):
        steam_login.submit_guard(client, pending(), "12345", 2)
    client.post.assert_not_called()
    client.post.return_value = reply({})
    steam_login.submit_guard(
        client, {**pending(), "allowed_confirmations": [{"confirmation_type": 2}]}, "ABCDE", 2
    )
    values = json.loads(client.post.call_args.kwargs["data"]["input_json"])
    assert values["steamid"] == pending()["steamid"]
    assert values["code_type"] == 2


def test_provider_sign_in_errors_do_not_echo_response_secrets():
    client = Mock()
    client.post.return_value = reply({"secret": "fixture-password"}, result="5")
    with pytest.raises(steam_session.SteamLibraryError) as error:
        steam_login.begin_qr(client)
    assert "fixture-password" not in str(error.value)


def test_poll_does_not_approve_account_agreements():
    client = Mock()
    client.post.return_value = reply(
        {"agreement_session_url": "https://store.steampowered.com/agreement"}
    )
    with pytest.raises(steam_session.SteamLibraryError, match="review its account agreement"):
        steam_login.poll(client, pending())


@pytest.mark.parametrize(
    "transfer_url",
    [
        "https://other.test/login/settoken",
        "https://store.steampowered.com.evil.test/login/settoken",
        "http://store.steampowered.com/login/settoken",
    ],
)
def test_refresh_exchange_never_forwards_credentials_to_arbitrary_transfers(
    monkeypatch, transfer_url
):
    payload = (
        base64.urlsafe_b64encode(
            json.dumps(
                {
                    "sub": "76561198000000001",
                    "exp": int(time.time()) + 3600,
                }
            ).encode()
        )
        .decode()
        .rstrip("=")
    )
    refresh = f"e30.{payload}.synthetic-signature"
    client = Mock()
    response = Mock(status_code=200)
    response.json.return_value = {
        "transfer_info": [{"url": transfer_url, "params": {"nonce": "n", "auth": "a"}}]
    }
    client.post.return_value = response
    context = Mock()
    context.__enter__ = Mock(return_value=client)
    context.__exit__ = Mock(return_value=False)
    monkeypatch.setattr(steam_login.requests, "Session", Mock(return_value=context))
    with pytest.raises(steam_session.SteamLibraryError):
        steam_login.exchange_refresh_token(refresh)
    client.post.assert_called_once()
    assert client.post.call_args.args[0] == "https://login.steampowered.com/jwt/finalizelogin"
    assert client.post.call_args.kwargs["allow_redirects"] is False
    client.get.assert_not_called()
