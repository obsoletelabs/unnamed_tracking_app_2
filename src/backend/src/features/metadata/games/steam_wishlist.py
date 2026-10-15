"""A player's Steam wishlist: the app ids they have wishlisted."""

import requests

from src.features.metadata.games.steam import _WEB_API_BASE, SESSION, SteamLibraryError
from src.features.metadata.games.steam_session import SteamSession, credential_params


def get_wishlist_app_ids(steam_id: str, api_key: str | SteamSession) -> list[int]:
    """The wishlisted app ids, most wanted first. An empty or hidden wishlist is
    just an empty list; rejected credentials and Steam failures are errors."""
    try:
        resp = SESSION.get(
            f"{_WEB_API_BASE}/IWishlistService/GetWishlist/v1/",
            params={**credential_params(api_key), "steamid": steam_id},
            timeout=20,
            allow_redirects=False,
        )
    except requests.RequestException:
        raise SteamLibraryError("Could not reach Steam. Try again shortly.") from None
    if resp.status_code in (401, 403):
        if isinstance(api_key, SteamSession):
            raise SteamLibraryError("Steam rejected this session. Reconnect Steam in Connections.")
        raise SteamLibraryError("Steam rejected the API key.")
    if resp.status_code >= 500:
        raise SteamLibraryError(f"Steam wishlist request failed ({resp.status_code}).")
    if resp.status_code >= 400:
        return []
    try:
        items = resp.json().get("response", {}).get("items", [])
    except ValueError:
        return []
    ranked = sorted(items, key=lambda i: i.get("priority") or 0)
    return [int(i["appid"]) for i in ranked if str(i.get("appid", "")).isdigit()]
