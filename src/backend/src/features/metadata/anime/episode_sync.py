"""Fetching a show's episode list from whichever provider has it, shared
between on-demand routes and background refresh through registered
metadata provider capabilities."""

from __future__ import annotations

from typing import Any, NamedTuple
from uuid import UUID

from src.database.session import SessionLocal
from src.features.metadata.identity import normalized_title
from src.features.metadata.service import collect_owned_record, library_candidate, search_records
from src.plugin_api.metadata_contracts import MediaType, MetadataCandidate


def anime_candidate(title: str, external_id: str | None, anilist_id: str | None,
                    kitsu_id: str | None = None):
    """Preserve known identities; a title-search Kitsu guess is not trusted for seasons."""
    ids = {key: value for key, value in
           (("mal", external_id), ("anilist", anilist_id), ("kitsu", kitsu_id)) if value}
    return library_candidate(title, MediaType.ANIME, ids)


async def fetch_episode_totals(user_id: UUID, title: str, external_id: str | None,
                               anilist_id: str | None) -> tuple[dict[str, Any] | None, list[str]]:
    """Translate canonical airing facts into the existing season reconciliation rules."""
    record, errors = await collect_owned_record(
        user_id, anime_candidate(title, external_id, anilist_id), resource="airing"
    )
    airing = record.get("metadata", {}).get("airing")
    if not airing:
        return None, errors
    status = airing.get("status")
    return {
        "status": {"ongoing": "RELEASING", "completed": "FINISHED"}.get(status, status),
        "total": airing.get("total_episodes") if status == "completed" else None,
        "planned": airing.get("total_episodes"), "next": airing.get("next_episode_number"),
    }, errors


def _merge_episode_sources(*sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Combines any number of providers' episode lists by episode number
    instead of picking one: no single provider is complete. Merged per field:
    the first source passed wins a field it has a value for, later sources
    only fill in whatever is still blank."""
    by_number: dict[int, dict[str, Any]] = {}
    for source in sources:
        for entry in source:
            existing = by_number.get(entry["episode_number"])
            if existing is None:
                by_number[entry["episode_number"]] = dict(entry)
                continue
            for key, value in entry.items():
                if value is not None and not existing.get(key):
                    existing[key] = value
    return sorted(by_number.values(), key=lambda e: e["episode_number"])


class EpisodeFetch(NamedTuple):
    episodes: list[dict[str, Any]]
    errors: list[str]
    # the entry's own episode count, only once it has finished airing
    final_total: int | None
    # the highest episode number that can exist: the final total, or for a
    # show still airing its announced total or what has aired so far
    limit: int | None
    # the exact Kitsu id ani.zip gives for this entry, if it knows one
    kitsu_id: str | None


def episode_limit(info: dict[str, Any] | None) -> int | None:
    """The highest episode number an AniList entry can have right now. A
    finished one has its final total. One still airing has what it announced,
    or failing that what has aired (the episode before the next one), because
    a provider listing a thousand more is listing episodes that do not exist
    yet. Unknown gives no limit."""
    if not info:
        return None
    if info.get("total"):
        return int(info["total"])
    if info.get("status") == "RELEASING":
        if info.get("planned"):
            return int(info["planned"])
        upcoming = info.get("next")
        if upcoming and upcoming > 1:
            return int(upcoming) - 1
    return None


def _is_complete(episodes: list[dict[str, Any]], final_total: int | None) -> bool:
    """Whether one provider's list already covers everything: every episode
    titled, and (for a finished show) every number up to its total."""
    if not episodes or any(not e.get("title") for e in episodes):
        return False
    if final_total is None:
        return True
    return {e["episode_number"] for e in episodes} >= set(range(1, final_total + 1))


async def fetch_episodes_with_fallback(
    external_id: str | None,
    anilist_id: str | None,
    kitsu_id: str | None = None,
    *,
    user_id: UUID,
    title: str,
    final_total: int | None = None,
    limit: int | None = None,
    total_known: bool = False,
) -> EpisodeFetch:
    """Fetch primary episodes first, then fill gaps through registered fallbacks.

    Cross-provider IDs returned by the primary are authoritative. A stored Kitsu
    title-search guess is never used for an entry already identified by AniList.
    Completed and ongoing entries keep their own episode ceilings. Provider
    transport, retries and parsing remain in the plugin repository.
    """
    candidate = anime_candidate(title, external_id, anilist_id,
                                kitsu_id if not anilist_id else None)
    primary, errors = await collect_owned_record(
        user_id, candidate, resource="episodes", episode_phase="primary"
    )
    if not total_known:
        info, total_errors = await fetch_episode_totals(user_id, title, external_id, anilist_id)
        errors.extend(total_errors)
        final_total = (info or {}).get("total")
        limit = episode_limit(info)
    episodes, fallback_errors = await _complete_episode_sources(
        user_id, candidate, primary, final_total
    )
    errors.extend(fallback_errors)
    if limit or final_total:
        episodes = [entry for entry in episodes
                    if entry["episode_number"] <= (limit or final_total or 0)]
    return EpisodeFetch(episodes, list(dict.fromkeys(errors)), final_total,
                        limit or final_total,
                        primary.get("metadata", {}).get("provider_ids", {}).get("kitsu"))


async def _complete_episode_sources(
    user_id: UUID, candidate: MetadataCandidate, primary: dict[str, Any], final_total: int | None,
) -> tuple[list[dict[str, Any]], list[str]]:
    episodes = primary.get("metadata", {}).get("episodes", [])
    if _is_complete(episodes, final_total):
        return _merge_episode_sources(episodes), []
    candidate = candidate.model_copy(update={
        "provider_ids": {**candidate.provider_ids,
                         **primary.get("metadata", {}).get("provider_ids", {})},
    })
    fallback, errors = await collect_owned_record(
        user_id, candidate, resource="episodes", episode_phase="fallback"
    )
    return _merge_episode_sources(episodes, fallback.get("metadata", {}).get("episodes", [])), errors


async def fetch_airing_status(
    anilist_id: str | None, *, user_id: UUID, title: str,
) -> tuple[int | None, bool, int | None, int | None, list[str]]:
    """A cheap airing resource never requests an episode inventory or artwork."""
    if not anilist_id:
        return None, False, None, None, []
    record, errors = await collect_owned_record(
        user_id, anime_candidate(title, None, anilist_id), resource="airing"
    )
    airing = record.get("metadata", {}).get("airing")
    if not airing:
        return None, False, None, None, errors or ["Metadata providers are unavailable"]
    return (airing.get("aired_episodes"), bool(airing.get("is_airing")),
            airing.get("next_episode_at"), airing.get("next_episode_number"), errors)


def pad_to_known_total(all_episodes: list[dict[str, Any]], episode_count: int | None) -> int | None:
    """Neither provider reliably lists every episode for a very
    long-running show — pad the rest as plain numbered placeholders up to
    `episode_count` (when known) so the checklist still covers the whole
    run. In place. Returns the total to store on the season (unchanged if
    already known, otherwise the highest episode number actually seen)."""
    known_numbers = {entry["episode_number"] for entry in all_episodes}
    if episode_count:
        for n in range(1, episode_count + 1):
            if n not in known_numbers:
                all_episodes.append({"episode_number": n})
        all_episodes.sort(key=lambda e: e["episode_number"])
        return episode_count
    if all_episodes:
        return max(known_numbers)
    return episode_count


_BACKFILLABLE_EPISODE_FIELDS = (
    "title",
    "description",
    "air_date",
    "runtime_minutes",
    "still_url",
)


def needs_tmdb_backfill(all_episodes: list[dict[str, Any]]) -> bool:
    """Whether any entry is still missing a field TMDB could fill —
    checked per field rather than just "no title yet", since an entry can
    already have a real title (from Jikan, which never returns an
    episode image at all) while still missing everything else."""
    return any(
        entry.get(field) is None for entry in all_episodes for field in _BACKFILLABLE_EPISODE_FIELDS
    )


async def backfill_from_metadata(
    all_episodes: list[dict[str, Any]], show_title: str, *, user_id: UUID,
) -> None:
    """Fill remaining episode fields from an unambiguous TV identity.

    This optional TV fallback uses scoped plugin configuration and the same
    canonical episode resource. Existing titles, dates and images remain intact.
    """
    async with SessionLocal() as db:
        response = await search_records(db, user_id, show_title, MediaType.TV_SHOW)
    matches = [record for record in response["results"]
               if normalized_title(record["title"]) == normalized_title(show_title)]
    if len(matches) != 1:
        return
    record, _ = await collect_owned_record(
        user_id, library_candidate(show_title, MediaType.TV_SHOW, matches[0]["provider_ids"]),
        resource="episodes", season_number=1,
    )
    entries = record.get("metadata", {}).get("episodes", [])
    by_number = {entry["episode_number"]: entry for entry in entries}
    for entry in all_episodes:
        match = by_number.get(entry["episode_number"])
        if not match:
            continue
        for field in _BACKFILLABLE_EPISODE_FIELDS:
            if entry.get(field) is None and match.get(field) is not None:
                entry[field] = match[field]
