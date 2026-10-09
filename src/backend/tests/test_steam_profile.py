"""Common Steam profile identifiers and unavailable library responses."""

from unittest.mock import Mock

import pytest

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
