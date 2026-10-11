"""Owner-session identity validation and conservative private progress reads."""

import base64
import json
import time
from unittest.mock import Mock

import pytest
import requests

from src.features.imports.steam_achievements import fetch_steam_achievements
from src.features.metadata.games import steam, steam_session

STEAM_ID = "76561198000000001"


def token(steam_id=STEAM_ID, expiry=None):
    payload = {"sub": steam_id, "exp": expiry if expiry is not None else int(time.time()) + 3600}
    encoded = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return f"e30.{encoded}.synthetic-signature"


def reply(body, *, result="1", status=200):
    response = Mock(status_code=status, headers={"x-eresult": result})
    response.json.return_value = body
    response.text = json.dumps(body)
    return response


@pytest.mark.parametrize("wrapped", [False, True])
def test_owner_is_verified_by_steam_not_just_jwt_claims(wrapped):
    value = token()
    client = Mock()
    client.get.return_value = reply({"response": {"steamid": STEAM_ID}})
    session = steam_session.validate_session(
        client, json.dumps({"data": {"webapi_token": value}}) if wrapped else value
    )
    assert session.steam_id == STEAM_ID
    assert value not in repr(session)
    assert client.get.call_args.args[0].endswith("/ISteamUserOAuth/GetTokenDetails/v1/")
    assert client.get.call_args.kwargs["params"] == {"access_token": value}


@pytest.mark.parametrize(
    "body",
    [{"success": 15, "reason": "secret"}, {"response": {"steamid": "76561198000000002"}}, {}],
)
def test_rejected_or_mismatched_owner_is_not_authenticated(body):
    client = Mock()
    client.get.return_value = reply(body)
    with pytest.raises(steam_session.SteamLibraryError):
        steam_session.validate_session(client, token())


@pytest.mark.parametrize(
    "value",
    ["not-a-session", "x" * 16385, token(expiry=0), token(expiry=True), token(steam_id="name")],
)
def test_invalid_and_expired_tokens_do_not_reach_steam(value):
    client = Mock()
    with pytest.raises(steam_session.SteamLibraryError):
        steam_session.validate_session(client, value)
    client.get.assert_not_called()


def test_network_failures_do_not_disclose_token_urls():
    client = Mock()
    value = token()
    client.get.side_effect = requests.ConnectionError(f"secret URL?access_token={value}")
    with pytest.raises(steam_session.SteamLibraryError) as error:
        steam_session.validate_session(client, value)
    assert value not in str(error.value)
    assert error.value.__suppress_context__


def definitions():
    return {
        "response": {
            "schema_hash": 123,
            "schema_version": 1,
            "achievements": [
                {
                    "internal_key": 7,
                    "internal_name": "WIN",
                    "localized_name": "Win",
                    "localized_desc": "Finish the game",
                    "hidden": True,
                },
                {"internal_key": 9, "internal_name": "EXTRA", "localized_name": "Extra"},
            ],
        }
    }


def test_private_achievements_use_owner_endpoints_and_existing_row_names():
    client = Mock()
    client.get.side_effect = [
        reply(definitions()),
        reply(
            {
                "response": {
                    "schema_hash": 123,
                    "schema_version": 1,
                    "achievements": [
                        {"internal_key": 9, "unlocked": False},
                        {"internal_key": 7, "unlocked": True, "unlock_time": 123456},
                    ],
                }
            }
        ),
    ]
    session = steam_session.SteamSession(STEAM_ID, int(time.time()) + 3600, token())
    schema, rows = steam_session.get_achievement_data(client, session, 123)
    assert schema["WIN"]["description"] == "Finish the game"
    assert schema["WIN"]["hidden"] is True
    assert rows == [
        {"apiname": "EXTRA", "achieved": 0, "unlocktime": 0},
        {"apiname": "WIN", "achieved": 1, "unlocktime": 123456},
    ]
    calls = client.get.call_args_list
    assert calls[0].args[0].endswith("/IPlayerService/GetGameAchievements/v1/")
    assert calls[1].args[0].endswith("/IPlayerService/GetUserAchievements/v1/")
    assert calls[1].kwargs["params"]["steamid"] == STEAM_ID
    assert "key" not in calls[1].kwargs["params"]


@pytest.mark.parametrize(
    "progress",
    [
        {},
        {"achievements": []},
        {"schema_hash": 124, "schema_version": 1, "achievements": []},
        {"schema_hash": 123, "schema_version": 1, "achievements": [{"internal_key": 7}]},
        {"schema_hash": 123, "schema_version": 1, "achievements": [{"internal_key": 999}]},
    ],
)
def test_partial_or_changed_achievement_data_is_unavailable(progress):
    client = Mock()
    client.get.side_effect = [reply(definitions()), reply({"response": progress})]
    session = steam_session.SteamSession(STEAM_ID, int(time.time()) + 3600, token())
    with pytest.raises(steam_session.SteamLibraryError):
        steam_session.get_achievement_data(client, session, 123)


def test_games_without_achievements_do_not_request_player_progress():
    client = Mock()
    client.get.return_value = reply({"response": {"achievements": []}})
    session = steam_session.SteamSession(STEAM_ID, int(time.time()) + 3600, token())
    assert steam_session.get_achievement_data(client, session, 123) == ({}, [])
    client.get.assert_called_once()


def test_owned_games_use_the_verified_session_without_a_personal_api_key(monkeypatch):
    request = Mock(
        return_value=reply(
            {
                "response": {
                    "game_count": 1,
                    "games": [
                        {"appid": 1230410, "name": "Chaos", "playtime_forever": 5},
                    ],
                }
            }
        )
    )
    monkeypatch.setattr(steam.SESSION, "get", request)
    session = steam.SteamSession(STEAM_ID, int(time.time()) + 3600, token())
    assert steam.resolve_steam_id(STEAM_ID, session) == STEAM_ID
    games = steam.get_owned_games(STEAM_ID, session)
    assert games[0]["appid"] == 1230410
    assert request.call_args.kwargs["params"]["access_token"] == session.access_token
    assert "key" not in request.call_args.kwargs["params"]
    with pytest.raises(steam.SteamLibraryError, match="different account"):
        steam.get_owned_games("76561198000000002", session)
    request.assert_called_once()


async def test_failed_owner_reads_keep_the_existing_unavailable_snapshot_contract(monkeypatch):
    request = Mock(return_value=reply({"response": {}}, result="15"))
    monkeypatch.setattr(steam.SESSION, "get", request)
    session = steam.SteamSession(STEAM_ID, int(time.time()) + 3600, token())
    assert await fetch_steam_achievements(STEAM_ID, session, 123) == ({}, None, None)
