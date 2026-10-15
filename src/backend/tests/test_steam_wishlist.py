"""Wishlist reads tell an empty wishlist apart from a rejected or failed request."""

from unittest.mock import Mock

import pytest

from src.features.metadata.games import steam_wishlist
from src.features.metadata.games.steam_session import SteamSession

KEY = "ABCDEF0123456789ABCDEF0123456789"
STEAM_ID = "76561197960287930"


def reply(status_code, payload=None):
    response = Mock(status_code=status_code)
    response.json.return_value = payload or {}
    return response


def test_wishlist_is_ranked_by_priority(monkeypatch):
    items = [{"appid": 20, "priority": 2}, {"appid": 10, "priority": 1}, {"appid": "x"}]
    monkeypatch.setattr(
        steam_wishlist.SESSION, "get", Mock(return_value=reply(200, {"response": {"items": items}}))
    )
    assert steam_wishlist.get_wishlist_app_ids(STEAM_ID, KEY) == [10, 20]


@pytest.mark.parametrize("status_code", [200, 400, 404])
def test_empty_or_hidden_wishlist_is_empty(monkeypatch, status_code):
    monkeypatch.setattr(steam_wishlist.SESSION, "get", Mock(return_value=reply(status_code)))
    assert steam_wishlist.get_wishlist_app_ids(STEAM_ID, KEY) == []


@pytest.mark.parametrize(
    ("credential", "status_code", "message"),
    [
        (KEY, 403, "rejected the API key"),
        (SteamSession(STEAM_ID, 2_000_000_000, "token"), 401, "Reconnect Steam"),
        (KEY, 503, r"wishlist request failed \(503\)"),
    ],
)
def test_rejected_or_failed_requests_are_errors(monkeypatch, credential, status_code, message):
    monkeypatch.setattr(steam_wishlist.SESSION, "get", Mock(return_value=reply(status_code)))
    with pytest.raises(steam_wishlist.SteamLibraryError, match=message):
        steam_wishlist.get_wishlist_app_ids(STEAM_ID, credential)
