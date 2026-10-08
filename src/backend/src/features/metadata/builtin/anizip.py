"""AniZip canonical episodes and cross-provider IDs for an already resolved anime."""

from datetime import datetime
from urllib.parse import urlencode

from src.features.metadata.core_http import ProviderHttp, metadata_page

PROVIDER_ID = "core.anizip"
DECLARATION = {
    "provider_id": PROVIDER_ID,
    "name": "AniZip",
    "media_types": ["anime"],
    "metadata_resources": ["entity", "episodes"],
    "episode_source": "primary",
    "operations": {"metadata": "metadata", "health": "health"},
}


async def lookup(work, ids):
    key = next((key for key in ("anilist", "mal") if ids.get(key)), None)
    if key is None:
        return None
    return await ProviderHttp(work, "anizip", min_gap=0.3)(
        "https://api.ani.zip/mappings?" + urlencode({key + "_id": ids[key]})
    )


async def metadata(values):
    work = values["request"]
    item = await lookup(work, values["candidate"].get("provider_ids", {}))
    if not item:
        return {}
    mappings = item.get("mappings") or {}
    ids = {
        namespace: str(mappings[key])
        for namespace, key in (
            ("anilist", "anilist_id"),
            ("mal", "mal_id"),
            ("kitsu", "kitsu_id"),
            ("tvdb", "thetvdb_id"),
        )
        if mappings.get(key)
    }
    if work.get("resource") == "episodes":
        episodes = []
        for key, entry in (item.get("episodes") or {}).items():
            if not str(key).isdigit() or not isinstance(entry, dict):
                continue
            limit = item.get("episodeCount")
            if isinstance(limit, int) and int(key) > limit > 0:
                continue
            titles = entry.get("title") or {}
            stamp = entry.get("airDateUtc")
            episodes.append(
                {
                    "episode_number": int(key),
                    "title": titles.get("en") or next(iter(titles.values()), None),
                    "description": (entry.get("overview") or entry.get("summary") or "")
                    .replace("`", "'")
                    .split("\nSource:", 1)[0]
                    .strip()
                    or None,
                    "air_date": entry.get("airDate") or entry.get("airdate"),
                    "air_at": int(datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp())
                    if stamp
                    else None,
                    "runtime_minutes": entry.get("runtime") or entry.get("length"),
                    "still_url": entry.get("image"),
                }
            )
        episodes.sort(key=lambda entry: entry["episode_number"])
        return metadata_page(work, "episodes", episodes, {"provider_ids": ids})
    return {"metadata": {"provider_ids": ids}}


async def health(values):
    await lookup(values["request"], {"anilist": "20"})
    return {"health": "healthy"}
