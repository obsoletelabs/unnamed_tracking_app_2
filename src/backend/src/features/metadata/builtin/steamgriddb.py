"""Selected-entity artwork from SteamGridDB, with host-owned credentials."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from src.features.metadata.core_http import ProviderFailure, configuration, network
from src.features.metadata.identity import normalized_title

PROVIDER_ID = "core.steamgriddb"
DECLARATION = {
    "provider_id": PROVIDER_ID,
    "name": "SteamGridDB",
    "media_types": ["game"],
    "operations": {"media": "media", "health": "health"},
    "configuration": [
        {
            "key": "api_key",
            "label": "SteamGridDB API key",
            "scope": "both",
            "required": True,
            "secret": True,
        }
    ],
}
BASE_URL = "https://www.steamgriddb.com/api/v2"


async def _request(path: str) -> dict[str, Any]:
    key = configuration(PROVIDER_ID).get("api_key")
    if not key:
        raise ProviderFailure("not_configured")
    data = await network(
        BASE_URL + path,
        headers={"Authorization": "Bearer " + key, "User-Agent": "unnamed-tracking-app/1.0"},
    )
    if not isinstance(data, dict) or data.get("success") is not True:
        raise ProviderFailure("unavailable")
    return data


async def media(values: dict[str, Any]) -> dict[str, Any]:
    """No artwork is fetched before the host issues an explicit media operation."""
    candidate = values["candidate"]
    try:
        steam_id = candidate.get("provider_ids", {}).get("steam")
        if steam_id:
            game = (await _request("/games/steam/" + quote(str(steam_id), safe="")))["data"]
        else:
            matches = (
                await _request("/search/autocomplete/" + quote(candidate["title"], safe=""))
            )["data"]
            game = next(
                (
                    item
                    for item in matches
                    if normalized_title(item.get("name", ""))
                    == normalized_title(candidate["title"])
                ),
                None,
            )
        if not game or game.get("id") is None:
            return {"assets": []}
        assets: list[dict[str, Any]] = []
        for endpoint, kind in (
            ("grids", "key_art"),
            ("heroes", "banner"),
            ("logos", "logo"),
            ("icons", "icon"),
        ):
            data = await _request(f"/{endpoint}/game/{game['id']}")
            for image in data.get("data", [])[:40]:
                if not image.get("url"):
                    continue
                if kind == "key_art" and image.get("width", 0) >= image.get("height", 0):
                    continue
                assets.append(
                    {
                        "kind": kind,
                        "url": image["url"],
                        "priority": len(assets),
                        "width": image.get("width") or None,
                        "height": image.get("height") or None,
                    }
                )
        return {"assets": assets}
    except ProviderFailure as exc:
        return exc.response()


async def health(_values: dict[str, Any]) -> dict[str, Any]:
    try:
        await _request("/games/steam/620")
        return {"health": "healthy"}
    except ProviderFailure as exc:
        return exc.response()
