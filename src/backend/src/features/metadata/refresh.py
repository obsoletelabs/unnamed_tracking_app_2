"""Daily background refresh of episode data for shows/anime that already
have episodes synced â€” catches newly aired episodes for shows still
airing. Same in-process asyncio loop pattern as the trash sweep
(features/trash/sweep.py) and automatic backups
(features/backup/scheduler.py): no new worker container, no new
dependency. Mostly appends episode numbers that aren't already stored
and enriches blank placeholders â€” a watched or rated episode is never
touched. The one exception: a still-blank placeholder beyond what the
source provider now says has actually aired gets pruned (see
_prune_unaired_episodes), since that only happens from a prior
miscalculation, never from a real episode disappearing."""

from __future__ import annotations

import logging
import time
from datetime import date
from typing import Any

from sqlalchemy import or_, select

from src.database.models.anime import Anime, AnimeEpisode, AnimeSeason, AnimeStatus
from src.database.models.tv_show import TVEpisode, TVSeason, TVShow, TVShowStatus
from src.database.session import SessionLocal
from src.features.episode_progress import materialize_progress
from src.features.metadata.anime.episode_sync import (
    anime_candidate,
    backfill_from_metadata,
    fetch_airing_status,
    fetch_episodes_with_fallback,
    needs_tmdb_backfill,
    pad_to_known_total,
)
from src.features.metadata.service import (
    collect_owned_record,
    media_result,
    resolve_library_record,
)
from src.features.metadata.tv.episode_sync import (
    fetch_is_airing,
    fetch_next_episode,
    fetch_season_episodes,
)
from src.plugin_api.metadata_contracts import MediaType

logger = logging.getLogger(__name__)

REFRESH_INTERVAL_SECONDS = 24 * 60 * 60

# an airing show with no announced next episode is looked at this often, and
# any airing show at least this often, in case its schedule moved
RECHECK_UNKNOWN_SECONDS = 6 * 60 * 60
RECHECK_SCHEDULED_SECONDS = 24 * 60 * 60
# when each show was last asked about (in memory: a restart checks everything once)
_last_checked: dict[str, float] = {}


def _add_episode(
    model: type[AnimeEpisode] | type[TVEpisode],
    season_id,
    entry: dict,
    *,
    include_air_at: bool = True,
) -> AnimeEpisode | TVEpisode:
    raw_air_date = entry.get("air_date")
    return model(
        season_id=season_id,
        episode_number=entry["episode_number"],
        title=entry.get("title"),
        description=entry.get("description"),
        air_date=date.fromisoformat(raw_air_date) if raw_air_date else None,
        runtime_minutes=entry.get("runtime_minutes"),
        still_url=entry.get("still_url"),
        air_at=entry.get("air_at") if include_air_at else None,
    )


def _enrich_episode(existing: AnimeEpisode | TVEpisode, entry: dict) -> bool:
    """Fill in whatever the placeholder row is still missing from a fresh
    fetch â€” title being the main gate (a row synced before a TMDB key
    existed stays a bare "Episode N" until this runs again), but any
    other still-blank field is picked up too. Never overwrites a field
    that's already set. Returns whether anything actually changed."""
    changed = False
    if existing.title is None and entry.get("title") is not None:
        existing.title = entry["title"]
        changed = True
    if existing.description is None and entry.get("description") is not None:
        existing.description = entry["description"]
        changed = True
    if existing.air_date is None and entry.get("air_date"):
        existing.air_date = date.fromisoformat(entry["air_date"])
        changed = True
    if existing.runtime_minutes is None and entry.get("runtime_minutes") is not None:
        existing.runtime_minutes = entry["runtime_minutes"]
        changed = True
    if existing.air_at is None and entry.get("air_at") is not None:
        existing.air_at = entry["air_at"]
        changed = True
    if existing.still_url is None and entry.get("still_url") is not None:
        existing.still_url = entry["still_url"]
        changed = True
    return changed


def _prune_unaired_episodes(season: AnimeSeason, valid_numbers: set[int]) -> int:
    """Removes a still-blank placeholder row whose episode number the
    source provider no longer includes as aired â€” the only way that
    happens is a prior aired-count miscalculation created it too early
    (e.g. padding to a season's confirmed total instead of how many had
    actually aired). Never touches a watched or rated episode, and only
    called after a successful fetch, so a transient provider hiccup
    can't be mistaken for episodes disappearing."""
    removed = 0
    for episode in list(season.episodes):
        if episode.episode_number in valid_numbers:
            continue
        if episode.watched or episode.rating is not None:
            continue
        if episode.title is not None:
            continue
        season.episodes.remove(episode)
        removed += 1
    return removed


def _trim_beyond_total(season: AnimeSeason, total: int) -> int:
    """A season whose entry has finished airing with `total` episodes cannot
    have episode `total + 1`: rows numbered past it came from a provider that
    matched the wrong entry (season 1's list attached to season 2). They are
    removed unless the user watched or rated them, and the season's own count
    is set to the real total (or to the highest row that had to be kept)."""
    removed = 0
    for episode in list(season.episodes):
        if episode.episode_number <= total:
            continue
        if episode.watched or episode.rating is not None:
            continue
        season.episodes.remove(episode)
        removed += 1
    kept_top = max((e.episode_number for e in season.episodes), default=0)
    season.episode_count = max(total, kept_top)
    season.episodes_watched = min(season.episodes_watched or 0, season.episode_count)
    return removed


def merge_episodes(
    db,
    season: AnimeSeason | TVSeason,
    entries: list[dict[str, Any]],
    model: type[AnimeEpisode] | type[TVEpisode],
    *,
    include_air_at: bool = True,
) -> tuple[int, int]:
    """Append new rows and fill blank fields without replacing user progress."""
    existing_by_number = {e.episode_number: e for e in season.episodes}
    added = 0
    enriched = 0
    created: list[AnimeEpisode | TVEpisode] = []
    for entry in entries:
        existing = existing_by_number.get(entry["episode_number"])
        if existing is None:
            row = _add_episode(model, season.id, entry, include_air_at=include_air_at)
            db.add(row)
            created.append(row)
            added += 1
        elif _enrich_episode(existing, entry):
            enriched += 1
    materialize_progress(season, created)
    return added, enriched


async def refresh_anime_season_now(
    db,
    show: Anime,
    season: AnimeSeason,
    *,
    final_total: int | None = None,
    limit: int | None = None,
    total_known: bool = False,
) -> tuple[int, int]:
    """Returns (added, enriched) â€” added is brand-new episode numbers
    that didn't exist yet; enriched is existing bare placeholder rows
    that just got a real title/image now that better data is available
    (e.g. a TMDB key was added after the first sync). Also repairs a season
    a wrong provider match had inflated (see `_trim_beyond_total`)."""
    if not (show.external_id or show.anilist_id or show.kitsu_id):
        return 0, 0
    fetch = await fetch_episodes_with_fallback(
        show.external_id,
        show.anilist_id,
        show.kitsu_id,
        final_total=final_total,
        limit=limit,
        total_known=total_known,
        user_id=show.user_id,
        title=show.title,
    )
    all_episodes, errors = fetch.episodes, fetch.errors
    if fetch.kitsu_id and show.kitsu_id != fetch.kitsu_id:
        # ani.zip's exact id replaces one found by title search, which is how
        # season 2 ended up with season 1's Kitsu entry
        show.kitsu_id = fetch.kitsu_id
    if errors:
        logger.warning(
            "Anime refresh couldn't reach a provider for %r: %s", show.title, "; ".join(errors)
        )
    if not all_episodes:
        if fetch.limit:
            _trim_beyond_total(season, fetch.limit)
        return 0, 0
    # A finished entry is padded to its own total. Otherwise pad only up to
    # THIS fetch's highest episode number, never the stored total: feeding
    # that back in would re-inflate every fresh fetch to the same wrong number.
    target = fetch.final_total or max((e["episode_number"] for e in all_episodes), default=None)
    season.episode_count = pad_to_known_total(all_episodes, target)
    if needs_tmdb_backfill(all_episodes):
        await backfill_from_metadata(all_episodes, show.title, user_id=show.user_id)
    added, enriched = merge_episodes(db, season, all_episodes, AnimeEpisode)
    if fetch.limit:
        _trim_beyond_total(season, fetch.limit)
    # a show still airing has its rows kept in step by the airing check, which
    # adds up to what AniList says has aired; pruning here as well made the two
    # undo each other on every run
    if not errors and show.is_airing is not True:
        _prune_unaired_episodes(season, {e["episode_number"] for e in all_episodes})
    return added, enriched


async def quick_check_anime_season(show: Anime, season: AnimeSeason) -> int:
    """The cheap check for one anime: asks AniList how many episodes have
    aired (and whether it is still airing at all), and if the count has gone
    up, adds bare numbered placeholder rows (no title or thumbnail yet) so
    there is something to check off right away. The full refresh fills in real
    titles and images later. Only works for anime: MyAnimeList/Jikan has no
    comparably cheap endpoint."""
    if not show.anilist_id:
        return 0
    aired_total, is_airing, air_at, next_number, errors = await fetch_airing_status(
        show.anilist_id, user_id=show.user_id, title=show.title
    )
    if errors:
        logger.warning(
            "Airing check couldn't reach AniList for %r: %s", show.title, "; ".join(errors)
        )
        return 0
    return await _apply_airing(
        show,
        season,
        aired_total=aired_total,
        is_airing=is_airing,
        air_at=air_at,
        next_number=next_number,
    )


async def _apply_airing(
    show: Anime,
    season: AnimeSeason,
    *,
    aired_total: int | None,
    is_airing: bool,
    air_at: int | None,
    next_number: int | None,
) -> int:
    """Stores what AniList says about a show (persisting `is_airing` either way,
    so a finished show is left alone from then on) and adds the placeholder
    rows for episodes that have aired. Returns how many rows were added."""
    show.is_airing = is_airing
    show.next_episode_air_at = air_at
    show.next_episode_number = next_number
    if not aired_total:
        return 0
    known_max = max((e.episode_number for e in season.episodes), default=0)
    added = 0
    if aired_total > known_max:
        for n in range(known_max + 1, aired_total + 1):
            season.episodes.append(AnimeEpisode(season_id=season.id, episode_number=n))
            added += 1
        materialize_progress(season)
        if season.episode_count is None or season.episode_count < aired_total:
            season.episode_count = aired_total
    if is_airing and show.anilist_id:
        await _fill_recent_episodes(show, season)
    return added


async def _fill_recent_episodes(show: Anime, season: AnimeSeason) -> None:
    """One ani.zip call fills the title, screenshot, synopsis and exact
    air time of the newest episodes, which is what makes a freshly aired
    episode look finished within a check cycle instead of staying a bare
    "Episode N" until the daily full refresh. Only runs when one of the
    newest few episodes is actually missing something."""
    recent = sorted(season.episodes, key=lambda e: e.episode_number)[-4:]
    if not any(e.title is None or e.still_url is None or e.air_at is None for e in recent):
        return
    record, errors = await collect_owned_record(
        show.user_id,
        anime_candidate(show.title, show.external_id, show.anilist_id),
        resource="episodes",
        episode_phase="primary",
    )
    if errors:
        logger.warning("Episode providers unavailable while filling recent episodes")
    entries = record.get("metadata", {}).get("episodes", [])
    by_number = {e["episode_number"]: e for e in entries}
    for episode in recent:
        entry = by_number.get(episode.episode_number)
        if entry:
            _enrich_episode(episode, entry)


async def quick_check_tv_season(show: TVShow, season: TVSeason, db) -> int:
    """The frequent, cheap check for TV: first asks TVmaze just the
    show's status (a single small object) and persists `show.is_airing`
    from it. Only when it's actually still running does it bother with
    the (still cheap, but heavier) full episode fetch â€” a finished show
    gets excluded from the query entirely on the next pass instead of
    being re-fetched every cycle forever."""
    if not show.external_id:
        return 0
    is_airing, errors = await fetch_is_airing(
        show.external_id, user_id=show.user_id, title=show.title
    )
    if errors:
        logger.warning(
            "Airing check couldn't reach TVmaze for %r: %s", show.title, "; ".join(errors)
        )
        return 0
    if is_airing is not None:
        show.is_airing = is_airing
    if is_airing is False:
        show.next_episode_air_at = None
        show.next_episode_number = None
        return 0
    air_at, next_number, next_errors = await fetch_next_episode(
        show.external_id, user_id=show.user_id, title=show.title
    )
    if not next_errors:
        show.next_episode_air_at = air_at
        show.next_episode_number = next_number
    added, _enriched = await refresh_tv_season_now(show, season, db)
    return added


async def refresh_tv_season_now(show: TVShow, season: TVSeason, db) -> tuple[int, int]:
    """Returns (added, enriched) â€” TV rarely has bare placeholders (no
    padding step for TV, unlike anime), but the same enrich pass runs
    anyway so a row TVmaze had gaps in the first time still gets picked
    up if the gap closes later."""
    if not show.external_id:
        return 0, 0
    all_episodes, errors = await fetch_season_episodes(
        show.external_id, season.season_number, user_id=show.user_id, title=show.title
    )
    if errors:
        logger.warning("TV refresh couldn't reach TVmaze for %r: %s", show.title, "; ".join(errors))
    return merge_episodes(db, season, all_episodes, TVEpisode)


# (show attribute, entry key) pairs backfilled only when the show's own
# field is still blank â€” id fields are handled separately since they
# need str(...) conversion and the season episode count lives one level
# down, not a plain attribute of `show` itself.
# Healing and MAL imports deliberately select different subsets of the same provider fields.
# pylint: disable=duplicate-code
_HEALABLE_FIELDS: tuple[tuple[str, str], ...] = (
    ("format", "format"),
    ("poster_url", "poster_url"),
    ("backdrop_url", "backdrop_url"),
    ("description", "description"),
    ("studios", "studios"),
    ("genres", "genres"),
    ("episode_runtime_minutes", "episode_runtime_minutes"),
)
# pylint: enable=duplicate-code


async def _heal_anime_metadata(db, show: Anime) -> bool:
    """Backfills whichever of format/poster/backdrop/description/studios/
    genres/runtime/score/anilist_id/external_id/kitsu_id are still null.
    Recovers whichever of the AniList/MyAnimeList ids was still missing
    from `_find_anime_entry` â€” AniList's own data carries MyAnimeList's
    id for the same entry (`idMal`), and a show missing that id can only
    ever get Jikan's richer per-episode data (titles/synopses/air-dates)
    once it's recovered. Kitsu has no such cross-reference, so its id is
    recovered separately via its own exact-title search. Returns whether
    anything actually changed."""
    changed = False

    candidate = anime_candidate(show.title, show.external_id, show.anilist_id)
    if show.first_air_date:
        candidate = candidate.model_copy(update={"year": show.first_air_date.year})
    record, _ = await resolve_library_record(db, show.user_id, candidate, include_media=True)
    entry = media_result(record, MediaType.ANIME) if record else None
    if entry:
        if show.anilist_id is None and entry.get("anilist_id"):
            show.anilist_id = str(entry["anilist_id"])
            changed = True
        if show.external_id is None and entry.get("mal_id"):
            show.external_id = str(entry["mal_id"])
            changed = True
        for attr, key in _HEALABLE_FIELDS:
            if not getattr(show, attr) and entry.get(key):
                setattr(show, attr, entry[key])
                changed = True
        if show.anilist_score is None and entry.get("anilist_score") is not None:
            show.anilist_score = entry["anilist_score"]
            changed = True
        if entry.get("episode_count") and show.seasons and show.seasons[0].episode_count is None:
            show.seasons[0].episode_count = entry["episode_count"]
            changed = True

    # A Kitsu id is never guessed from the title here: a title search matched
    # season 1's entry for season 2. ani.zip gives the exact id when episodes
    # are refreshed (see `refresh_anime_season_now`).
    return changed


async def heal_all_anime_metadata() -> int:
    """Sweeps every non-deleted anime row for one missing enough to be
    worth a lookup (no format, poster, AniList id, MyAnimeList id, or
    Kitsu id yet) and backfills it â€” the self-healing counterpart to the
    manual "delete and re-add" fix a title with a failed metadata match
    used to need. Missing `external_id`/`kitsu_id` are included because
    they silently cap episode quality forever otherwise: without them,
    episode sync can only ever use whichever provider's id it does have,
    never merge in the other two's data. A show with NEITHER AniList nor
    MyAnimeList id (added by hand, or added while both providers were
    unreachable) is not skipped either â€” `_heal_anime_metadata` falls
    back to an exact-title search for those, which is the majority of
    what's actually stuck with zero episode data, since episode sync has
    nothing to look up at all without at least one real id. A small
    pause between rows paces requests the same way the relations chain
    walk does, so a large backlog doesn't burn through AniList's rate
    limit in one burst."""
    healed = 0
    async with SessionLocal() as db:
        shows = (
            (
                await db.execute(
                    select(Anime).where(
                        Anime.deleted_at.is_(None),
                        or_(
                            Anime.format.is_(None),
                            Anime.poster_url.is_(None),
                            Anime.anilist_id.is_(None),
                            Anime.external_id.is_(None),
                        ),
                    )
                )
            )
            .scalars()
            .all()
        )
        for show in shows:
            try:
                if await _heal_anime_metadata(db, show):
                    healed += 1
            # Isolate unexpected provider data failures to this show while continuing the sweep.
            # pylint: disable-next=broad-exception-caught
            except Exception:
                logger.exception("Anime metadata heal failed for %s", show.title)
        await db.commit()
    if healed:
        logger.info("Metadata heal: backfilled %d anime row(s)", healed)
    return healed


async def refresh_all_episode_metadata() -> dict[str, int]:
    """The full refresh, run to the end (the daily loop uses this). It only
    touches what needs it; see refresh_job for the details and for the version
    the Settings button starts with progress. If a run is already going, this
    returns that run's progress instead of starting another."""
    # The job uses the refresh operations above; defer its coordinator import until called.
    # pylint: disable-next=import-outside-toplevel,cyclic-import
    from src.features.metadata import refresh_job

    progress = await refresh_job.run("needed")
    return {
        "anime_episodes_added": progress["anime_episodes_added"],
        "anime_episodes_updated": progress["anime_episodes_updated"],
        "tv_episodes_added": progress["tv_episodes_added"],
        "tv_episodes_updated": progress["tv_episodes_updated"],
        "anime_metadata_healed": progress["anime_metadata_healed"],
        "counts_fixed": progress["counts_fixed"],
    }


def airing_due(
    is_airing: bool | None, next_air_at: int | None, last_checked: float | None, now: float
) -> bool:
    """Whether a show is worth asking a provider about right now. A new
    episode can only appear once the scheduled one has aired, so a show whose
    next episode is still in the future is left alone, except for an occasional
    look in case the schedule moved. One never checked is always due."""
    if is_airing is None:
        return True
    if next_air_at and next_air_at <= now:
        return True
    limit = RECHECK_SCHEDULED_SECONDS if next_air_at else RECHECK_UNKNOWN_SECONDS
    return last_checked is None or now - last_checked >= limit


async def _check_anime_airing(db, force: bool, now: float) -> tuple[int, int, int]:
    anime_added = checked = 0
    anime_rows = (
        await db.execute(
            select(Anime.id, Anime.anilist_id, Anime.is_airing, Anime.next_episode_air_at).where(
                Anime.deleted_at.is_(None),
                Anime.status != AnimeStatus.DROPPED,
                or_(Anime.is_airing.is_(True), Anime.is_airing.is_(None)),
            )
        )
    ).all()
    due = {
        r.id: int(r.anilist_id)
        for r in anime_rows
        if r.anilist_id
        and str(r.anilist_id).isdigit()
        and (
            force
            or airing_due(r.is_airing, r.next_episode_air_at, _last_checked.get(str(r.id)), now)
        )
    }
    if due:
        shows = (await db.execute(select(Anime).where(Anime.id.in_(list(due))))).scalars().all()
        for anime_show in shows:
            season = anime_show.seasons[-1] if anime_show.seasons else None
            if season is None or not season.episodes:
                continue
            try:
                anime_added += await quick_check_anime_season(anime_show, season)
                _last_checked[str(anime_show.id)] = now
                checked += 1
            # Isolate unexpected provider data failures to this show while continuing the sweep.
            # pylint: disable-next=broad-exception-caught
            except Exception:
                logger.exception("Airing check failed for anime %s", anime_show.title)

    return anime_added, checked, len(anime_rows)


async def _check_tv_airing(db, force: bool, now: float) -> tuple[int, int, int]:
    tv_added = checked = 0
    tv_rows = (
        await db.execute(
            select(TVShow.id, TVShow.is_airing, TVShow.next_episode_air_at).where(
                TVShow.deleted_at.is_(None),
                TVShow.status != TVShowStatus.DROPPED,
                or_(TVShow.is_airing.is_(True), TVShow.is_airing.is_(None)),
            )
        )
    ).all()
    tv_due = [
        r.id
        for r in tv_rows
        if force
        or airing_due(r.is_airing, r.next_episode_air_at, _last_checked.get(str(r.id)), now)
    ]
    if tv_due:
        tv_shows = (await db.execute(select(TVShow).where(TVShow.id.in_(tv_due)))).scalars().all()
        for tv_show in tv_shows:
            tv_season = tv_show.seasons[-1] if tv_show.seasons else None
            if tv_season is None or not tv_season.episodes:
                continue
            try:
                tv_added += await quick_check_tv_season(tv_show, tv_season, db)
                _last_checked[str(tv_show.id)] = now
                checked += 1
            # Isolate unexpected provider data failures to this show while continuing the sweep.
            # pylint: disable-next=broad-exception-caught
            except Exception:
                logger.exception("Airing check failed for TV show %s", tv_show.title)

    return tv_added, checked, len(tv_rows)


async def check_airing_episodes(force: bool = False) -> dict[str, int]:
    """The frequent, lightweight pass over shows that are airing or not yet
    checked (`is_airing` true or null); one known to have finished, or a
    dropped one, is skipped entirely. Only the shows that are due (see
    `airing_due`, or all of them with `force`) are asked about: anime in one
    AniList request per 50 shows, TV one show at a time. Their episodes are
    loaded only then, not for every airing show on every pass. Neither does the
    TMDB backfill, which stays on the slower full refresh."""
    now = time.time()
    async with SessionLocal() as db:
        anime_added, anime_checked, anime_candidates = await _check_anime_airing(db, force, now)
        tv_added, tv_checked, tv_candidates = await _check_tv_airing(db, force, now)
        checked = anime_checked + tv_checked
        candidates = anime_candidates + tv_candidates
        await db.commit()

    if anime_added or tv_added:
        logger.info(
            "Airing check added %d anime episode(s), %d TV episode(s)", anime_added, tv_added
        )
    return {
        "anime_episodes_added": anime_added,
        "tv_episodes_added": tv_added,
        "checked": checked,
        "not_due": max(0, candidates - checked),
    }
