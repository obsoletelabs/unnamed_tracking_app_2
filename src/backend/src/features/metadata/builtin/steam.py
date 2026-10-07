"""Steam storefront core provider; the host owns ranking, selection and persistence."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from urllib.parse import urlencode

from src.features.metadata.core_http import OPERATIONS, ProviderFailure, ProviderHttp, external_id
from src.features.metadata.games.steam_tags import parse_tags, pick_genre_tags

PROVIDER_ID = "core.steam"
DECLARATION = {
    "provider_id": PROVIDER_ID,
    "name": "Steam",
    "media_types": ["game"],
    "identifier_namespace": "steam",
    "operations": dict(OPERATIONS),
}


async def search(values: dict[str, Any]) -> dict[str, Any]:
    """Store search only; no app details, community tags or artwork calls."""
    work = values["request"]
    try:
        data = await ProviderHttp(work, "steam", min_gap=0.15)(
            "https://store.steampowered.com/api/storesearch?"
            + urlencode({"term": work["query"], "l": "english", "cc": "us"})
        )
        candidates = []
        for item in data.get("items", [])[: work.get("limit", 20)]:
            if not item.get("name") or item.get("id") is None:
                continue
            candidates.append(
                {
                    "provider": PROVIDER_ID,
                    "external_id": str(item["id"]),
                    "title": item["name"],
                    "media_type": "game",
                    "provider_ids": {"steam": str(item["id"])},
                }
            )
        return {"candidates": candidates}
    except ProviderFailure as exc:
        return exc.response()


async def metadata(values: dict[str, Any]) -> dict[str, Any]:
    """Preserve partial identity even when optional fields are absent."""
    candidate = values["candidate"]
    app_id = candidate.get("provider_ids", {}).get("steam")
    if app_id is None and candidate.get("provider") == PROVIDER_ID:
        app_id = candidate["external_id"]
    if app_id is None:
        return {}
    try:
        http = ProviderHttp(values["request"], "steam", min_gap=0.15)
        data = (
            await http(
                "https://store.steampowered.com/api/appdetails?"
                + urlencode({"appids": app_id, "l": "english", "cc": "us"})
            )
        ).get(str(app_id), {})
        if not data.get("success"):
            return {"metadata": {"provider_ids": {"steam": str(app_id)}}}
        details = data.get("data", {})
        patch = {
            "title": details.get("name") or candidate["title"],
            "description": details.get("about_the_game")
            or details.get("detailed_description")
            or details.get("short_description"),
            "developer": ", ".join(details.get("developers", []))[:200] or None,
            "publisher": ", ".join(details.get("publishers", []))[:200] or None,
            "tags": [
                item["description"] for item in details.get("genres", []) if item.get("description")
            ],
            "features": [
                item["description"]
                for item in details.get("categories", [])
                if item.get("description")
            ],
            "age_rating": f"{details['required_age']}+" if details.get("required_age") else None,
            "provider_ids": {"steam": str(app_id)},
            "links": [
                {"label": "Steam Store", "url": f"https://store.steampowered.com/app/{app_id}/"}
            ],
        }
        if values["request"].get("options", {}).get("steam_user_tags", True):
            try:
                html = await http(
                    f"https://store.steampowered.com/app/{app_id}/?l=english",
                    response_format="text",
                    headers={
                        "User-Agent": "Mozilla/5.0 (compatible; unnamed-tracking-app/1.0)",
                        "Cookie": "birthtime=631152001; lastagecheckage=1-January-1990; wants_mature_content=1",
                    },
                )
                patch["tags"] = pick_genre_tags(parse_tags(html), patch["tags"])
            except ProviderFailure:
                pass
        release = details.get("release_date", {}).get("date", "")
        for date_format in ("%d %b, %Y", "%b %d, %Y", "%Y", "%Y-%m-%d"):
            try:
                parsed = datetime.strptime(release, date_format).date()
                patch["year"] = parsed.year
                if date_format != "%Y":
                    patch["release_date"] = parsed.isoformat()
                break
            except ValueError:
                continue
        return {"metadata": patch}
    except ProviderFailure as exc:
        return exc.response()


def media(values):
    """Return CDN references only after selection; missing files are host download fallbacks."""
    app_id = external_id(values["candidate"], PROVIDER_ID, "steam")
    if not app_id:
        return {}
    base = f"https://cdn.akamai.steamstatic.com/steam/apps/{app_id}"
    return {
        "assets": [
            {"kind": kind, "url": base + "/" + name, "priority": 200}
            for kind, name in (
                ("key_art", "library_600x900.jpg"),
                ("banner", "library_hero.jpg"),
                ("logo", "logo.png"),
                ("banner", "header.jpg"),
            )
        ]
    }


async def health(values: dict[str, Any]) -> dict[str, Any]:
    result = await search({"request": {**values["request"], "query": "Portal", "limit": 1}})
    return result if result.get("failure") else {"health": "healthy"}
