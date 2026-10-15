"""Common Steam profile identifiers and unavailable library responses."""

from unittest.mock import Mock

import pytest
import requests

from src.features.metadata.games import steam


@pytest.mark.parametrize(
    ("pasted", "expected"),
    [
        ("https://steamcommunity.com/id/gabelogannewell/", "gabelogannewell"),
        ("steamcommunity.com/id/someone", "someone"),
        ("https://www.steamcommunity.com/id/someone/", "someone"),
        ("https://steamcommunity.com/profiles/76561197960287930", "76561197960287930"),
        (
            "https://steamcommunity.com/profiles/76561197960287930/games/?tab=all#top",
            "76561197960287930",
        ),
        ("STEAM_0:0:11101", "76561197960287930"),
        ("STEAM_1:0:11101", "76561197960287930"),
        ("STEAM_0:1:11101", "76561197960287931"),
        ("[U:1:22202]", "76561197960287930"),
        ("U:1:22202", "76561197960287930"),
        ("76561197960287930", "76561197960287930"),
        ("  vanity_name  ", "vanity_name"),
    ],
)
def test_profile_spellings(pasted, expected) -> None:
    assert steam.parse_steam_identifier(pasted) == expected


@pytest.mark.parametrize(
    "identifier",
    [
        "https://steamcommunity.com.example.test/id/someone",
        "https://example.test/steamcommunity.com/id/someone",
        "https://steamcommunity.com@other.example/id/someone",
        "https://steamcommunity.com/id/",
        "[U:1:22202",
        "U:1:22202]",
        "[U:1:4294967296]",
        "STEAM_0:0:2147483648",
        "STEAM_0:0:0",
    ],
)
def test_malformed_identifiers_do_not_use_a_network_lookup(monkeypatch, identifier) -> None:
    lookup = Mock()
    monkeypatch.setattr(steam.SESSION, "get", lookup)
    with pytest.raises(steam.SteamLibraryError, match="profile|Steam ID"):
        steam.resolve_steam_id(identifier, "key")
    lookup.assert_not_called()


def test_numeric_profile_link_needs_no_lookup(monkeypatch) -> None:
    lookup = Mock()
    monkeypatch.setattr(steam.SESSION, "get", lookup)
    assert (
        steam.resolve_steam_id("https://steamcommunity.com/profiles/76561197960287930/", "key")
        == "76561197960287930"
    )
    lookup.assert_not_called()


def test_vanity_link_uses_only_its_name(monkeypatch) -> None:
    reply = Mock(status_code=200)
    reply.json.return_value = {"response": {"success": 1, "steamid": "76561197960287930"}}
    lookup = Mock(return_value=reply)
    monkeypatch.setattr(steam.SESSION, "get", lookup)
    assert steam.resolve_steam_id("https://steamcommunity.com/id/gaben/", "key") == (
        "76561197960287930"
    )
    assert lookup.call_args.kwargs["params"]["vanityurl"] == "gaben"


@pytest.mark.parametrize("payload", [{"response": {}}, {}])
def test_private_library_is_not_an_empty_library(monkeypatch, payload) -> None:
    reply = Mock(status_code=200)
    reply.json.return_value = payload
    monkeypatch.setattr(steam.SESSION, "get", Mock(return_value=reply))
    with pytest.raises(steam.SteamLibraryError, match="Game details to Public"):
        steam.get_owned_games("76561197960287930", "key")


@pytest.mark.parametrize("payload", [{"game_count": 0}, {"games": [], "game_count": 0}])
def test_explicitly_empty_public_library_is_empty(monkeypatch, payload) -> None:
    reply = Mock(status_code=200)
    reply.json.return_value = {"response": payload}
    monkeypatch.setattr(steam.SESSION, "get", Mock(return_value=reply))
    assert steam.get_owned_games("76561197960287930", "key") == []


@pytest.mark.parametrize("status_code", [401, 403])
def test_rejected_api_key_is_reported(monkeypatch, status_code) -> None:
    monkeypatch.setattr(steam.SESSION, "get", Mock(return_value=Mock(status_code=status_code)))
    with pytest.raises(steam.SteamLibraryError, match="rejected the API key"):
        steam.get_owned_games("76561197960287930", "key")


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {"response": None},
        {"response": {"games": None}},
        {"response": {"games": [], "game_count": 2}},
    ],
)
def test_invalid_library_response_is_a_provider_error(monkeypatch, payload) -> None:
    reply = Mock(status_code=200)
    reply.json.return_value = payload
    monkeypatch.setattr(steam.SESSION, "get", Mock(return_value=reply))
    with pytest.raises(steam.SteamLibraryError, match="invalid"):
        steam.get_owned_games("76561197960287930", "key")


def test_library_request_includes_unvetted_and_free_sub_apps(monkeypatch) -> None:
    games = [{"appid": 1230410, "name": "Chaos", "playtime_forever": 0}]
    reply = Mock(status_code=200)
    reply.json.return_value = {"response": {"games": games, "game_count": 1}}
    request = Mock(return_value=reply)
    monkeypatch.setattr(steam.SESSION, "get", request)
    assert steam.get_owned_games("76561197960287930", "key") == games
    params = request.call_args.kwargs["params"]
    assert params["include_free_sub"] == "1"
    assert params["skip_unvetted_apps"] == "0"


@pytest.mark.parametrize(
    "payload",
    [
        {"games": [{"appid": 123}], "game_count": 2},
        {"games": [{"appid": 123}], "game_count": 0},
        {"games": [{"appid": 123}], "game_count": True},
        {"games": [{"appid": 123}, {"appid": 123}], "game_count": 2},
        {"games": [{"appid": 0}]},
        {"games": [{"appid": True}]},
        {"games": [{"appid": "123"}]},
        {"games": [{"appid": 123, "name": []}]},
        {"games": [{"appid": 123, "playtime_forever": -1}]},
        {"games": [{"appid": 123, "playtime_forever": True}]},
        {"games": [{"appid": 123, "rtime_last_played": "yesterday"}]},
    ],
)
def test_incomplete_or_invalid_inventory_is_rejected(monkeypatch, payload) -> None:
    reply = Mock(status_code=200)
    reply.json.return_value = {"response": payload}
    monkeypatch.setattr(steam.SESSION, "get", Mock(return_value=reply))
    with pytest.raises(steam.SteamLibraryError, match="invalid"):
        steam.get_owned_games("76561197960287930", "key")


def test_library_request_failure_does_not_expose_the_api_key(monkeypatch) -> None:
    failure = requests.ConnectionError("https://api.steampowered.com/?key=private-key")
    monkeypatch.setattr(steam.SESSION, "get", Mock(side_effect=failure))
    with pytest.raises(steam.SteamLibraryError) as error:
        steam.get_owned_games("76561197960287930", "private-key")
    assert "private-key" not in str(error.value)
    assert "Try again" in str(error.value)


@pytest.mark.parametrize(
    "call",
    [
        lambda: steam.resolve_steam_id("vanity-name", "ABCDEF0123456789ABCDEF0123456789"),
        lambda: steam.get_schema_for_game("ABCDEF0123456789ABCDEF0123456789", 620),
        lambda: steam.get_player_achievements(
            "76561197960287930", "ABCDEF0123456789ABCDEF0123456789", 620
        ),
    ],
)
def test_network_errors_never_echo_the_api_key(monkeypatch, call) -> None:
    error = requests.ConnectionError(
        "Max retries exceeded with url: /?key=ABCDEF0123456789ABCDEF0123456789"
    )
    monkeypatch.setattr(steam.SESSION, "get", Mock(side_effect=error))
    with pytest.raises(steam.SteamLibraryError) as raised:
        call()
    assert "ABCDEF0123456789" not in str(raised.value)
    assert raised.value.__cause__ is None
