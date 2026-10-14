"""Public catalogue and artwork reuse the host's built-in Steam provider."""

import asyncio
import time
import unicodedata

from fastapi import HTTPException

from src.features.metadata.builtin import steam
from src.plugin_api.outbound import public_image_request

_cache: dict[str, tuple[float, dict]] = {}
_limit = asyncio.Semaphore(4)


def _title_key(title: str) -> str:
    return "".join(
        char for char in unicodedata.normalize("NFKC", title).casefold() if char.isalnum()
    )


async def game_info(title: str, app_id: str | None = None) -> dict:
    key = app_id or _title_key(title)
    previous = _cache.get(key)
    if previous and previous[0] > time.monotonic():
        return previous[1]
    work = {
        "query": title,
        "limit": 10,
        "policy": "interactive",
        "options": {"steam_user_tags": False},
    }
    async with _limit:
        if app_id:
            candidate = {"title": title, "provider_ids": {"steam": app_id}}
        else:
            result = await steam.search({"request": work})
            matches = [
                item
                for item in result.get("candidates", [])
                if _title_key(item["title"]) == _title_key(title)
            ]
            if len(matches) != 1:
                return {"title": title, "matched": False}
            candidate = matches[0]
        values = {"request": work, "candidate": candidate}
        result = await steam.metadata(values)
        patch = result.get("metadata", {})
        info = {
            "title": patch.get("title", title),
            "matched": bool(patch.get("title")),
            "provider_ids": patch.get("provider_ids", candidate.get("provider_ids", {})),
            "genres": patch.get("tags", []),
            "features": patch.get("features", []),
            "developer": patch.get("developer"),
            "release_date": patch.get("release_date"),
            "description": patch.get("description"),
            "links": patch.get("links", []),
        }
    if len(_cache) >= 128:
        _cache.pop(next(iter(_cache)))
    _cache[key] = (time.monotonic() + 3600, info)
    return info


async def artwork(app_id: str) -> dict:
    assets = steam.media({"candidate": {"provider_ids": {"steam": app_id}}}).get("assets", [])
    for asset in assets:
        if asset["kind"] not in {"key_art", "banner"}:
            continue
        try:
            return await asyncio.to_thread(public_image_request, asset["url"])
        except (OSError, ValueError, TimeoutError):
            continue
    raise HTTPException(404, "Public cover artwork unavailable.")
