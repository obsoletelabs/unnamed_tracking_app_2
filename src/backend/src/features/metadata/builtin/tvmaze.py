"""Keyless TVmaze capabilities, including canonical episode and airing metadata."""

import re
from datetime import datetime
from urllib.parse import urlencode

from src.features.metadata.core_http import OPERATIONS, ProviderHttp, external_id, metadata_page

PROVIDER_ID = "core.tvmaze"
DECLARATION = {
    "provider_id": PROVIDER_ID,
    "name": "TVmaze",
    "identifier_namespace": "tvmaze",
    "media_types": ["tv_show"],
    "metadata_resources": ["entity", "episodes", "airing"],
    "operations": dict(OPERATIONS),
}


async def get(work, path):
    return await ProviderHttp(work, "tvmaze", min_gap=0.15)("https://api.tvmaze.com" + path)


async def identity(item):
    ids = {"tvmaze": str(item["id"])}
    for name, value in (item.get("externals") or {}).items():
        if value and name in {"imdb", "thetvdb"}:
            ids["tvdb" if name == "thetvdb" else name] = str(value)
    return {
        "provider": PROVIDER_ID,
        "external_id": str(item["id"]),
        "title": item["name"],
        "media_type": "tv_show",
        "provider_ids": ids,
        "year": int(item["premiered"][:4]) if item.get("premiered") else None,
    }


async def search(values):
    work = values["request"]
    items = await get(work, "/search/shows?" + urlencode({"q": work["query"]}))
    return {
        "candidates": [
            await identity(item["show"])
            for item in items[: work.get("limit", 20)]
            if item.get("show", {}).get("name")
        ]
    }


async def show(values):
    entity_id = external_id(values["candidate"], PROVIDER_ID, "tvmaze")
    return (
        await get(values["request"], f"/shows/{entity_id}?embed=nextepisode") if entity_id else None
    )


async def metadata(values):
    item = await show(values)
    if item is None:
        return {}
    work = values["request"]
    if work.get("resource") == "episodes":
        entries = await get(work, f"/shows/{item['id']}/episodes?specials=1")
        episodes = []
        for entry in entries:
            if (
                work.get("season_number") is not None
                and entry.get("season") != work["season_number"]
            ):
                continue
            number = entry.get("number")
            if not isinstance(number, int):
                continue
            episodes.append(
                {
                    "episode_number": number,
                    "season_number": entry.get("season"),
                    "title": entry.get("name"),
                    "description": entry.get("summary"),
                    "air_date": entry.get("airdate") or None,
                    "air_at": int(datetime.fromisoformat(entry["airstamp"]).timestamp())
                    if entry.get("airstamp")
                    else None,
                    "runtime_minutes": entry.get("runtime"),
                    "still_url": (entry.get("image") or {}).get("original"),
                }
            )
        return metadata_page(work, "episodes", episodes)
    upcoming = (item.get("_embedded") or {}).get("nextepisode") or {}
    airing = {
        "status": {"Running": "ongoing", "Ended": "completed"}.get(item.get("status"), "paused"),
        "is_airing": item.get("status") == "Running",
        "next_episode_number": upcoming.get("number"),
        "next_episode_at": int(datetime.fromisoformat(upcoming["airstamp"]).timestamp())
        if upcoming.get("airstamp")
        else None,
    }
    if work.get("resource") == "airing":
        return {"metadata": {"airing": airing}}
    network = item.get("network") or item.get("webChannel") or {}
    return {
        "metadata": {
            "title": item.get("name"),
            "release_date": item.get("premiered"),
            "description": re.sub("<[^>]+>", "", item.get("summary") or "").strip() or None,
            "episode_runtime_minutes": item.get("averageRuntime") or item.get("runtime"),
            "studios": [network["name"]] if network.get("name") else [],
            "genres": item.get("genres") or [],
            "provider_ids": (await identity(item))["provider_ids"],
            "airing": airing,
            "seasons": [
                {
                    "season_number": entry["number"],
                    "title": entry.get("name"),
                    "episode_count": entry.get("episodeOrder"),
                    "air_date": entry.get("premiereDate") or None,
                }
                for entry in await get(work, f"/shows/{item['id']}/seasons")
                if isinstance(entry.get("number"), int)
            ][:100],
            "scores": {"tvmaze": item["rating"]["average"] * 10}
            if (item.get("rating") or {}).get("average") is not None
            else {},
            "links": [{"label": "TVmaze", "url": item["url"]}] if item.get("url") else [],
        }
    }


async def media(values):
    item = await show(values)
    image = (item or {}).get("image") or {}
    url = image.get("original") or image.get("medium")
    return {"assets": [{"kind": "poster", "url": url}] if url else []}


async def health(values):
    result = await search({"request": {**values["request"], "query": "The Office", "limit": 1}})
    return result if result.get("failure") else {"health": "healthy"}
