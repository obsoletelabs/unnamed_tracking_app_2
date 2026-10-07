"""AniList GraphQL search, enrichment, episode inventory and airing capabilities."""

import re

from src.features.metadata.builtin.anilist_franchise import FranchiseGraph
from src.features.metadata.core_http import (
    OPERATIONS,
    ProviderFailure,
    ProviderHttp,
    external_id,
    metadata_page,
)

PROVIDER_ID = "core.anilist"
DECLARATION = {
    "provider_id": PROVIDER_ID,
    "name": "AniList",
    "identifier_namespace": "anilist",
    "media_types": ["anime"],
    "metadata_resources": ["entity", "episodes", "airing", "relations", "recommendations"],
    "operations": dict(OPERATIONS),
}
IDENTITY = "id idMal title { romaji english native } startDate { year }"
DETAILS = """id idMal title { romaji english native } description(asHtml: false)
startDate { year month day } episodes duration format genres countryOfOrigin averageScore siteUrl
studios(isMain: true) { nodes { name } } status nextAiringEpisode { episode airingAt }"""


async def graphql(work, query, **variables):
    result = await ProviderHttp(work, "anilist", min_gap=0.7)(
        "https://graphql.anilist.co", method="POST", body={"query": query, "variables": variables}
    )
    if result.get("errors"):
        raise ProviderFailure("unavailable")
    return result.get("data") or {}


def identity(item):
    titles = item.get("title") or {}
    title = titles.get("english") or titles.get("romaji") or titles.get("native")
    ids = {"anilist": str(item["id"])}
    if item.get("idMal"):
        ids["mal"] = str(item["idMal"])
    return {
        "provider": PROVIDER_ID,
        "external_id": str(item["id"]),
        "media_type": "anime",
        "title": title,
        "alternate_titles": list(dict.fromkeys((value for value in titles.values() if value))),
        "year": (item.get("startDate") or {}).get("year"),
        "provider_ids": ids,
    }


async def search(values):
    work = values["request"]
    result = await graphql(
        work,
        "query($search:String,$limit:Int){Page(page:1,perPage:$limit){media(search:$search,type:ANIME){"
        + IDENTITY
        + "}}}",
        search=work["query"],
        limit=work.get("limit", 20),
    )
    return {
        "candidates": [
            identity(item)
            for item in (result.get("Page") or {}).get("media", [])
            if any((item.get("title") or {}).values())
        ]
    }


async def entity(values, fields):
    candidate = values["candidate"]
    entity_id = external_id(candidate, PROVIDER_ID, "anilist")
    mal_id = candidate.get("provider_ids", {}).get("mal")
    if entity_id:
        query = "query($id:Int){Media(id:$id,type:ANIME){" + fields + "}}"
        result = await graphql(values["request"], query, id=int(entity_id))
    elif mal_id:
        query = "query($id:Int){Media(idMal:$id,type:ANIME){" + fields + "}}"
        result = await graphql(values["request"], query, id=int(mal_id))
    else:
        return None
    return result.get("Media")


def airing(item):
    upcoming = item.get("nextAiringEpisode") or {}
    running = item.get("status") == "RELEASING"
    return {
        "status": {
            "RELEASING": "ongoing",
            "FINISHED": "completed",
            "HIATUS": "paused",
            "CANCELLED": "cancelled",
        }.get(item.get("status"), "unknown"),
        "is_airing": running,
        "total_episodes": item.get("episodes"),
        "aired_episodes": max(0, upcoming["episode"] - 1)
        if upcoming.get("episode")
        else item.get("episodes")
        if item.get("status") == "FINISHED"
        else None,
        "next_episode_number": upcoming.get("episode"),
        "next_episode_at": upcoming.get("airingAt"),
    }


async def metadata(values):
    work = values["request"]
    resource = work.get("resource", "entity")
    if resource in {"relations", "recommendations"}:
        return await relations_metadata(values)
    fields = DETAILS
    if resource == "episodes":
        fields = "id episodes status nextAiringEpisode { episode airingAt } streamingEpisodes { title thumbnail }"
    item = await entity(values, fields)
    if item is None:
        return {}
    state = airing(item)
    if resource == "airing":
        return {"metadata": {"airing": state}}
    if resource == "episodes":
        episodes = []
        for number, entry in enumerate(item.get("streamingEpisodes", []), start=1):
            title = re.sub(
                "^Episode\\s+\\d+\\s*-\\s*", "", entry.get("title") or "", flags=re.I
            ).strip()
            episodes.append(
                {
                    "episode_number": number,
                    "title": None if title.casefold() == "untitled" else title or None,
                    "still_url": entry.get("thumbnail"),
                }
            )
        total = state.get("aired_episodes") or 0
        for number in range(len(episodes) + 1, min(total, 10000) + 1):
            episodes.append({"episode_number": number})
        return metadata_page(work, "episodes", episodes, {"airing": state})
    start = item.get("startDate") or {}
    release = (
        f"{start['year']:04d}-{start['month']:02d}-{start['day']:02d}"
        if all((start.get(name) for name in ("year", "month", "day")))
        else None
    )
    candidate = identity(item)
    return {
        "metadata": {
            "title": candidate["title"],
            "year": candidate["year"],
            "release_date": release,
            "description": item.get("description"),
            "episode_count": item.get("episodes"),
            "episode_runtime_minutes": item.get("duration"),
            "format": item.get("format"),
            "genres": item.get("genres") or [],
            "countries": [item["countryOfOrigin"]] if item.get("countryOfOrigin") else [],
            "studios": [entry["name"] for entry in (item.get("studios") or {}).get("nodes", [])],
            "scores": {"anilist": item["averageScore"]}
            if item.get("averageScore") is not None
            else {},
            "provider_ids": candidate["provider_ids"],
            "alternate_titles": candidate["alternate_titles"],
            "titles": {key: title for key, title in (item.get("title") or {}).items() if title},
            "airing": state,
            "links": [{"label": "AniList", "url": item["siteUrl"]}] if item.get("siteUrl") else [],
        }
    }


async def media(values):
    item = await entity(values, "id coverImage { extraLarge large } bannerImage") or {}
    cover = item.get("coverImage") or {}
    return {
        "assets": [
            {"kind": kind, "url": url}
            for kind, url in (
                ("poster", cover.get("extraLarge") or cover.get("large")),
                ("banner", item.get("bannerImage")),
            )
            if url
        ]
    }


async def health(values):
    result = await search({"request": {**values["request"], "query": "Naruto", "limit": 1}})
    return result if result.get("failure") else {"health": "healthy"}


async def relations_metadata(values):
    work = values["request"]
    candidate = values["candidate"]
    graph = await FranchiseGraph(work).relations_chain_and_branches(
        candidate["title"], external_id(candidate, PROVIDER_ID, "anilist")
    )
    relations = []
    for collection, group in (
        ("chain", "chain"),
        ("branches", "branch"),
        ("recommendations", "recommendation"),
    ):
        for entry in graph[collection]:
            if not entry.get("title") or not entry.get("id"):
                continue
            relations.append(
                {
                    "candidate": {
                        "provider": PROVIDER_ID,
                        "external_id": str(entry["id"]),
                        "title": entry["title"],
                        "media_type": "anime",
                        "year": entry.get("year"),
                        "provider_ids": {"anilist": str(entry["id"])},
                    },
                    "relation": entry.get("relation_label")
                    or ("Sequence" if group == "chain" else "Recommended"),
                    "group": group,
                    "parent_external_id": str(entry["anchor_id"])
                    if entry.get("anchor_id")
                    else None,
                    "parent_group": "branch"
                    if entry.get("anchor_kind") == "branch"
                    else "chain"
                    if entry.get("anchor_id")
                    else None,
                    "is_current": entry.get("is_current", False),
                    "format": entry.get("format"),
                    "episode_count": entry.get("episode_count"),
                    "poster_url": entry.get("poster_url"),
                }
            )
    return metadata_page(work, "relations", relations)
