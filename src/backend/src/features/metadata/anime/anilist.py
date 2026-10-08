from __future__ import annotations

import re
from typing import Any

import requests

from src.features.metadata.anime.anilist_relations import (
    format_label,
)
from src.features.metadata.anime.franchise import FranchiseTraversal, run_sync
from src.features.metadata.rate_limit import RateLimitError, request_with_backoff

_URL = "https://graphql.anilist.co"
_TAG_RE = re.compile(r"<[^>]+>")
# AniList's public API rate limit is low and shared across every client
# hitting it, not just this app — a single Related-tab load can already
# mean a dozen+ requests (one per prequel/sequel chain hop, one per
# branch-group reorder lookup), so a 429 is a routine, expected response
# under normal use, not a rare edge case. Retried with backoff (honoring
# `Retry-After` when AniList sends one) instead of surfacing a 502 to the
# user for something that just needed a short wait.
# A small pause between successive requests in a multi-request sequence
# (chain walk, branch-group reordering) so a long chain doesn't burn
# through the rate limit in one burst before any 429 has a chance to
# happen — cheaper than always waiting for the retry backoff.
_PACING_SECONDS = 0.3
# Shared between the search-by-title query and the lookup-by-id query
# below, so a single Media node's fields aren't kept in sync by hand in
# two places.
_MEDIA_FIELDS = """
      id
      idMal
      title {
        romaji
        english
        native
      }
      format
      description(asHtml: false)
      startDate {
        year
        month
        day
      }
      episodes
      duration
      studios(isMain: true) {
        nodes {
          name
        }
      }
      countryOfOrigin
      genres
      coverImage {
        extraLarge
        large
      }
      bannerImage
      averageScore
      siteUrl
"""
_QUERY = f"""
query ($search: String, $perPage: Int) {{
  Page(page: 1, perPage: $perPage) {{
    media(search: $search, type: ANIME) {{
{_MEDIA_FIELDS}
    }}
  }}
}}
"""
# Looks up one exact entry by AniList's own id — used when adding a title
# to the library from somewhere that already carries a real AniList id
# (a relations-graph branch, a chain entry, a recommendation) instead of
# re-searching by title, which can miss or mismatch for a title AniList
# itself would format slightly differently in its search index.
_BY_ID_QUERY = f"""
query ($id: Int) {{
  Media(id: $id, type: ANIME) {{
{_MEDIA_FIELDS}
  }}
}}
"""
# Same lookup, but keyed by MyAnimeList's id (Jikan's own id for the
# entry) — used to recover a missing AniList id for an entry that was
# added while AniList itself was unreachable/rate-limited and only Jikan
# matched, without depending on a fresh title search.
_BY_MAL_ID_QUERY = f"""
query ($idMal: Int) {{
  Media(idMal: $idMal, type: ANIME) {{
{_MEDIA_FIELDS}
  }}
}}
"""


class AniListError(RuntimeError):
    """Raised when AniList responds unsuccessfully."""


def _clean_description(value: str | None) -> str | None:
    if not value:
        return None
    return _TAG_RE.sub("", value).strip() or None


def _format_date(start_date: dict[str, Any] | None) -> str | None:
    if not start_date or not start_date.get("year"):
        return None
    year = start_date["year"]
    month = start_date.get("month") or 1
    day = start_date.get("day") or 1
    return f"{year:04d}-{month:02d}-{day:02d}"


# Up to 50 entries by MyAnimeList id in one request, so filling in a whole
# imported list takes a handful of calls instead of one per title.
_BY_MAL_IDS_QUERY = f"""
query ($ids: [Int], $perPage: Int) {{
  Page(page: 1, perPage: $perPage) {{
    media(idMal_in: $ids, type: ANIME) {{
{_MEDIA_FIELDS}
    }}
  }}
}}
"""
_BY_IDS_QUERY = f"""
query ($ids: [Int], $perPage: Int) {{
  Page(page: 1, perPage: $perPage) {{
    media(id_in: $ids, type: ANIME) {{
{_MEDIA_FIELDS}
    }}
  }}
}}
"""
_TOTALS_QUERY = """
query ($ids: [Int], $perPage: Int) {
  Page(page: 1, perPage: $perPage) {
    media(id_in: $ids, type: ANIME) {
      id
      episodes
      status
      nextAiringEpisode {
        episode
        airingAt
      }
    }
  }
}
"""
_BATCH_SIZE = 50


def _map_media_entry(entry: dict[str, Any]) -> dict[str, Any]:
    """Normalizes one `_MEDIA_FIELDS`-shaped node into the search-result
    dict shape — shared by `search()` (a page of these) and `get_by_id()`
    (exactly one), so the two stay in sync automatically."""
    title = entry.get("title") or {}
    cover = entry.get("coverImage") or {}
    studios = [n["name"] for n in (entry.get("studios") or {}).get("nodes", []) if n.get("name")]
    score = entry.get("averageScore")
    return {
        "id": entry.get("id"),
        "id_mal": entry.get("idMal"),
        "title": title.get("english") or title.get("romaji"),
        "title_english": title.get("english"),
        "title_romaji": title.get("romaji"),
        "title_native": title.get("native"),
        "overview": _clean_description(entry.get("description")),
        "release_date": _format_date(entry.get("startDate")),
        "episode_runtime_minutes": entry.get("duration"),
        "episode_count": entry.get("episodes"),
        "studios": studios,
        "countries": [entry["countryOfOrigin"]] if entry.get("countryOfOrigin") else [],
        "genres": entry.get("genres") or [],
        "poster_url": cover.get("extraLarge") or cover.get("large"),
        "backdrop_url": entry.get("bannerImage"),
        "score": (score / 10) if score is not None else None,
        "format": format_label(entry.get("format")),
        "url": entry.get("siteUrl"),
    }


_EPISODES_QUERY = """
query ($id: Int) {
  Media(id: $id, type: ANIME) {
    episodes
    nextAiringEpisode {
      episode
    }
    streamingEpisodes {
      title
      thumbnail
    }
  }
}
"""
# Same two fields the full episode fetch uses to work out the aired
# total, without the streamingEpisodes list — cheap enough to poll every
# few minutes across a whole library instead of only once a day.
_AIRED_COUNT_QUERY = """
query ($id: Int) {
  Media(id: $id, type: ANIME) {
    episodes
    nextAiringEpisode {
      episode
      airingAt
    }
  }
}
"""
# A streaming-episode title usually looks like "Episode 12 - The Real Folk
# Blues" — split off the leading "Episode N" so the stored title matches
# what Jikan would have given us, instead of keeping the number baked in.
_EPISODE_TITLE_RE = re.compile(r"^Episode\s+\d+\s*-\s*(.+)$", re.IGNORECASE)


def _blank_episode(
    episode_number: int, still_url: str | None = None, title: str | None = None
) -> dict[str, Any]:
    return {
        "episode_number": episode_number,
        "title": title,
        "description": None,
        "air_date": None,
        "runtime_minutes": None,
        "still_url": still_url,
    }


def _parse_streaming_episodes(streaming: list[dict[str, Any]]) -> list[dict[str, Any]]:
    results = []
    for i, entry in enumerate(streaming, start=1):
        raw_title = entry.get("title") or ""
        match = _EPISODE_TITLE_RE.match(raw_title)
        title = match.group(1) if match else (raw_title or None)
        # AniList's streaming partners often supply a thumbnail with no
        # real title, and AniList fills the gap with the literal string
        # "Untitled" rather than leaving it blank — treated as no title at
        # all so a richer one (Jikan, TMDB) still gets a chance to fill it
        # in on merge, instead of this placeholder counting as "already
        # has a title" and blocking every other source.
        if title and title.strip().lower() == "untitled":
            title = None
        results.append(_blank_episode(i, still_url=entry.get("thumbnail"), title=title))
    return results


def _aired_total(media: dict[str, Any]) -> int | None:
    """How many episodes have actually aired so far. A season can have a
    confirmed total episode count (e.g. 14) while still airing weekly
    (e.g. only 12 out) — `nextAiringEpisode`, when present, is what's
    actually aired and takes priority over the confirmed total. Only
    fall back to `episodes` once AniList reports nothing left scheduled,
    meaning the show has genuinely wrapped."""
    next_airing = media.get("nextAiringEpisode")
    if next_airing and next_airing.get("episode"):
        return next_airing["episode"] - 1
    if media.get("episodes"):
        return media["episodes"]
    return None


def _pad_to_aired_total(results: list[dict[str, Any]], aired_total: int | None) -> None:
    """Fill in plain numbered placeholders for every episode number up to
    `aired_total` that streamingEpisodes didn't cover, in place."""
    if not aired_total:
        return
    known = {r["episode_number"] for r in results}
    for n in range(1, aired_total + 1):
        if n not in known:
            results.append(_blank_episode(n))
    results.sort(key=lambda r: r["episode_number"])


_RELATIONS_QUERY = """
query ($search: String) {
  Media(search: $search, type: ANIME) {
    id
    title {
      romaji
      english
    }
    format
    episodes
    startDate {
      year
    }
    coverImage {
      extraLarge
      large
    }
    relations {
      edges {
        relationType(version: 2)
        node {
          id
          title {
            romaji
            english
          }
          format
          episodes
          startDate {
            year
          }
          coverImage {
            extraLarge
            large
          }
        }
      }
    }
    recommendations(sort: RATING_DESC, perPage: 10) {
      nodes {
        mediaRecommendation {
          id
          title {
            romaji
            english
          }
          format
          episodes
          startDate {
            year
          }
          coverImage {
            extraLarge
            large
          }
        }
      }
    }
  }
}
"""
# Same shape as _RELATIONS_QUERY but looked up by AniList's own id rather
# than a text search — used while walking the prequel/sequel chain, where
# every step after the first already has a real id to follow instead of
# a title to (re-)search for.
_RELATIONS_BY_ID_QUERY = """
query ($id: Int) {
  Media(id: $id, type: ANIME) {
    id
    title {
      romaji
      english
    }
    format
    episodes
    startDate {
      year
    }
    coverImage {
      extraLarge
      large
    }
    relations {
      edges {
        relationType(version: 2)
        node {
          id
          title {
            romaji
            english
          }
          format
          episodes
          startDate {
            year
          }
          coverImage {
            extraLarge
            large
          }
        }
      }
    }
    recommendations(sort: RATING_DESC, perPage: 10) {
      nodes {
        mediaRecommendation {
          id
          title {
            romaji
            english
          }
          format
          episodes
          startDate {
            year
          }
          coverImage {
            extraLarge
            large
          }
        }
      }
    }
  }
}
"""


class AniListClient:
    """Minimal client for AniList's public GraphQL API. No authentication
    required for read-only search queries — OAuth is only needed to
    mutate a user's own AniList list, which this app never does."""

    def __init__(self, *, session: requests.Session | None = None) -> None:
        self.session = session or requests.Session()

    def _post_graphql(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        """Every AniList call funnels through here so the 429 retry/backoff
        (and error normalization) only has to be written once. Retries up
        to three times, sleeping `Retry-After` when AniList sends
        one, otherwise an increasing backoff — after that, raises a
        friendly rate-limit message instead of AniList's raw 429 body.
        Throttled process-wide (not just within this client instance) so
        the several background loops that each walk the anime library
        (episode refresh, airing check, metadata heal) don't independently
        burst AniList at the same moment and all collide on the same 429s
        — see rate_limit.py."""
        try:
            response = request_with_backoff(
                lambda: self.session.post(
                    _URL, json={"query": query, "variables": variables}, timeout=15
                ),
                provider="anilist",
                pacing_seconds=_PACING_SECONDS,
            )
        except requests.RequestException as exc:
            raise AniListError(f"Could not reach AniList: {exc}") from exc
        except RateLimitError as exc:
            raise AniListError(
                "AniList is rate-limiting requests right now — wait a bit and try again."
            ) from exc
        if response.status_code >= 400:
            raise AniListError(
                f"AniList request failed ({response.status_code}): {response.text[:200]}"
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise AniListError("AniList returned invalid JSON.") from exc
        if "errors" in payload:
            messages = "; ".join(e.get("message", "unknown error") for e in payload["errors"])
            raise AniListError(f"AniList returned an error: {messages}")
        return payload

    def search(self, query: str, limit: int = 8) -> list[dict[str, Any]]:
        if not query.strip():
            return []
        payload = self._post_graphql(_QUERY, {"search": query, "perPage": limit})
        media = (payload.get("data") or {}).get("Page", {}).get("media") or []
        return [_map_media_entry(entry) for entry in media[:limit]]

    def get_by_id(self, anilist_id: int) -> dict[str, Any] | None:
        """The same result shape `search()` returns, for exactly one
        already-known AniList id — used when adding a title that a
        relations-graph branch, chain entry, or recommendation already
        carries a real id for, so the add doesn't depend on a fresh
        title search finding (and correctly matching) the same entry."""
        payload = self._post_graphql(_BY_ID_QUERY, {"id": anilist_id})
        media = (payload.get("data") or {}).get("Media")
        if not media:
            return None
        return _map_media_entry(media)

    def get_by_mal_id(self, mal_id: int) -> dict[str, Any] | None:
        """Same result shape as `get_by_id`, keyed by MyAnimeList's id
        instead — recovers a missing AniList id for an entry that was
        matched via Jikan only (e.g. added while AniList was rate-limited)."""
        payload = self._post_graphql(_BY_MAL_ID_QUERY, {"idMal": mal_id})
        media = (payload.get("data") or {}).get("Media")
        if not media:
            return None
        return _map_media_entry(media)

    def _batched(
        self, query: str, ids: list[int], key: str
    ) -> tuple[dict[int, dict[str, Any]], int]:
        found: dict[int, dict[str, Any]] = {}
        failed = 0
        for start in range(0, len(ids), _BATCH_SIZE):
            chunk = ids[start : start + _BATCH_SIZE]
            try:
                payload = self._post_graphql(query, {"ids": chunk, "perPage": _BATCH_SIZE})
            except AniListError:
                failed += len(chunk)
                continue
            for media in ((payload.get("data") or {}).get("Page") or {}).get("media") or []:
                mapped = _map_media_entry(media)
                if mapped.get(key):
                    found[int(mapped[key])] = mapped
        return found, failed

    def get_by_mal_ids(self, mal_ids: list[int]) -> tuple[dict[int, dict[str, Any]], int]:
        """Entries for many MyAnimeList ids at once, keyed by MAL id, in the
        same shape as `get_by_id`. Ids AniList does not know are simply
        absent. Also returns how many ids sat in a batch that failed (rate
        limit or outage), so a caller can say so instead of pretending they
        were looked up."""
        return self._batched(_BY_MAL_IDS_QUERY, mal_ids, "id_mal")

    def final_totals(self, anilist_ids: list[int]) -> dict[int, dict[str, Any]]:
        """For many AniList ids at once: {id: {"total", "planned", "next", "status"}}.
        `total` is the entry's own episode count and only when it has finished
        airing (an airing or cancelled entry's count is a plan, not a fact),
        so it can safely be used to trim episodes some provider attached from
        another entry. `planned` is the count an airing entry announces and
        `next` the number of its next episode and `air_at` when it airs;
        `episodes` is the raw count AniList holds. A batch that fails is
        simply absent from the result."""
        out: dict[int, dict[str, Any]] = {}
        for start in range(0, len(anilist_ids), _BATCH_SIZE):
            chunk = anilist_ids[start : start + _BATCH_SIZE]
            try:
                payload = self._post_graphql(_TOTALS_QUERY, {"ids": chunk, "perPage": _BATCH_SIZE})
            except AniListError:
                continue
            for media in ((payload.get("data") or {}).get("Page") or {}).get("media") or []:
                episodes = media.get("episodes")
                finished = (
                    media.get("status") == "FINISHED" and isinstance(episodes, int) and episodes > 0
                )
                next_airing = media.get("nextAiringEpisode") or {}
                upcoming = next_airing.get("episode")
                out[int(media["id"])] = {
                    "total": episodes if finished else None,
                    # what an airing entry says it will have, when it says
                    "planned": episodes
                    if isinstance(episodes, int) and episodes > 0 and not finished
                    else None,
                    "next": upcoming if isinstance(upcoming, int) else None,
                    "air_at": next_airing.get("airingAt"),
                    "episodes": episodes if isinstance(episodes, int) else None,
                    "status": media.get("status"),
                }
        return out

    def get_by_ids(self, anilist_ids: list[int]) -> tuple[dict[int, dict[str, Any]], int]:
        """Same as `get_by_mal_ids`, keyed by AniList's own id."""
        return self._batched(_BY_IDS_QUERY, anilist_ids, "id")

    def episodes(self, anilist_id: str) -> list[dict[str, Any]]:
        """Episode data via AniList's `streamingEpisodes` — thumbnail +
        title only, no air date or synopsis (AniList doesn't track those
        per-episode). Thinner than Jikan's data, but it's a real fallback
        for when Jikan is unreachable, rather than showing nothing.

        `streamingEpisodes` alone is badly incomplete for long-running
        shows (e.g. only the first ~69 of 1000+ One Piece episodes) — for
        an ongoing show AniList's own `episodes` total is null too (there
        is no fixed total yet), so `nextAiringEpisode.episode - 1` is used
        as the real aired-so-far count when present, and the remainder is
        padded as plain numbered placeholders so the full aired run is at
        least trackable even without rich metadata for every episode."""
        try:
            anilist_id_int = int(anilist_id)
        except (TypeError, ValueError) as exc:
            raise AniListError(f"Invalid AniList id: {anilist_id!r}") from exc
        payload = self._post_graphql(_EPISODES_QUERY, {"id": anilist_id_int})

        media = (payload.get("data") or {}).get("Media")
        if not media:
            return []

        results = _parse_streaming_episodes(media.get("streamingEpisodes") or [])
        _pad_to_aired_total(results, _aired_total(media))
        return results

    def airing_status(self, anilist_id: str) -> tuple[int | None, bool, int | None, int | None]:
        """`(aired_episode_count, is_airing, next_episode_air_at,
        next_episode_number)` — the same fields `episodes()` uses to
        compute a total, without the `streamingEpisodes` list, so this is
        cheap enough to poll every few minutes across a whole library to
        catch a newly-aired episode quickly, instead of running the full
        (thumbnail-fetching, TMDB-backfilling) sync on that cadence.
        `is_airing` is just whether AniList still has a
        `nextAiringEpisode` scheduled; the air-at timestamp is what
        drives a countdown display and the calendar view."""
        try:
            anilist_id_int = int(anilist_id)
        except (TypeError, ValueError) as exc:
            raise AniListError(f"Invalid AniList id: {anilist_id!r}") from exc
        payload = self._post_graphql(_AIRED_COUNT_QUERY, {"id": anilist_id_int})

        media = (payload.get("data") or {}).get("Media")
        if not media:
            return None, False, None, None
        next_airing = media.get("nextAiringEpisode")
        is_airing = bool(next_airing and next_airing.get("episode"))
        air_at = next_airing.get("airingAt") if next_airing else None
        next_number = next_airing.get("episode") if next_airing else None
        return _aired_total(media), is_airing, air_at, next_number

    def _fetch_relations_node(
        self, *, media_id: int | None = None, search: str | None = None
    ) -> dict[str, Any] | None:
        """One request's worth of a single Media node: its own id/title
        plus its direct relations edges and recommendations — the unit
        the chain walk in `relations_chain_and_branches` is built from."""
        variables: dict[str, Any]
        if media_id is not None:
            query, variables = _RELATIONS_BY_ID_QUERY, {"id": media_id}
        else:
            query, variables = _RELATIONS_QUERY, {"search": search}
        payload = self._post_graphql(query, variables)
        return (payload.get("data") or {}).get("Media")

    def relations_chain_and_branches(
        self, title: str, anilist_id: str | None = None
    ) -> dict[str, Any]:
        """Use the common franchise workflow with the legacy synchronous transport."""
        return run_sync(
            FranchiseTraversal(AniListError).relations_chain_and_branches(title, anilist_id),
            self._fetch_relations_node,
        )
