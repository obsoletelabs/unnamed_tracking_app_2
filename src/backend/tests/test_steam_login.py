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
        "steamID": "76561198000000001",
        "transfer_info": [{"url": transfer_url, "params": {"nonce": "n", "auth": "a"}}],
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


def _browser_token(owner):
    claims = {"sub": owner, "exp": int(time.time()) + 3600}
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"e30.{payload}.synthetic-signature"


def _browser_reply(body):
    response = Mock(status_code=200, text=json.dumps(body))
    response.json.return_value = body
    return response


def _browser_exchange(monkeypatch, *, server_owner="76561198000000001"):
    owner = "76561198000000001"
    client = Mock()
    client.post.side_effect = [
        _browser_reply(
            {
                "steamID": server_owner,
                "transfer_info": [
                    {
                        "url": "https://store.steampowered.com/login/settoken",
                        "params": {"nonce": "fixture-nonce", "auth": "fixture-auth"},
                    }
                ],
            }
        ),
        _browser_reply({"result": 1}),
    ]
    client.get.return_value = _browser_reply(
        {"success": 1, "data": {"webapi_token": _browser_token(owner)}}
    )
    context = Mock()
    context.__enter__ = Mock(return_value=client)
    context.__exit__ = Mock(return_value=False)
    monkeypatch.setattr(steam_login.requests, "Session", Mock(return_value=context))
    return client, _browser_token(owner)


def test_store_exchange_verifies_owner_without_using_legacy_oauth_endpoint(monkeypatch):
    client, refresh = _browser_exchange(monkeypatch)
    session = steam_login.exchange_refresh_token(refresh)
    assert session.steam_id == "76561198000000001"
    assert session.access_token not in repr(session)
    assert client.post.call_count == 2
    assert client.post.call_args.kwargs["data"]["steamID"] == session.steam_id
    client.get.assert_called_once_with(
        steam_session.SESSION_PAGE, timeout=20, allow_redirects=False
    )


@pytest.mark.parametrize("owner", [None, "76561198000000002", True, "name"])
def test_exchange_rejects_missing_or_different_server_identity_before_transfer(monkeypatch, owner):
    client, refresh = _browser_exchange(monkeypatch, server_owner=owner)
    with pytest.raises(steam_session.SteamLibraryError, match="verify this account"):
        steam_login.exchange_refresh_token(refresh)
    client.post.assert_called_once()
    client.get.assert_not_called()


def test_exchange_rejects_store_token_for_another_owner(monkeypatch):
    client, refresh = _browser_exchange(monkeypatch)
    client.get.return_value = _browser_reply(
        {"success": 1, "data": {"webapi_token": _browser_token("76561198000000002")}}
    )
    with pytest.raises(steam_session.SteamLibraryError, match="verify the session's account"):
        steam_login.exchange_refresh_token(refresh)


@pytest.mark.parametrize("result", [None, 0, 15, True, "1"])
def test_exchange_rejects_failed_or_incomplete_store_transfer(monkeypatch, result):
    client, refresh = _browser_exchange(monkeypatch)
    responses = list(client.post.side_effect)
    client.post.side_effect = [responses[0], _browser_reply({"result": result})]
    with pytest.raises(steam_session.SteamLibraryError, match="complete the browser-session"):
        steam_login.exchange_refresh_token(refresh)
    client.get.assert_not_called()
