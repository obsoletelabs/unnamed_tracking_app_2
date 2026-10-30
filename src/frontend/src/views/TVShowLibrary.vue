<script setup lang="ts">
import { computed, onMounted } from "vue";
import { useKeptAlive } from "../utils/useKeptAlive";
import { useMediaLibraryPage } from "../composables/useMediaLibraryPage";
import {
  fetchTVShowsPage,
  tvLibraryRevision,
  updateTVShow,
  deleteTVShow,
  tvShowToInput,
  updateSeason,
} from "../services/tvShows";
import type { SeasonUpdateInput } from "../services/tvShows";
import type { TVShow, TVShowStatus } from "../types/tv_show";
import { localMediaImage } from "../utils/mediaImages";
import MediaLibraryView from "../components/library/MediaLibraryView.vue";
import {
  statusBucket,
  bucketToReal,
  resolveStatus,
} from "../utils/mediaStatus";
import type {
  LibraryCardVM,
  EditForm,
} from "../components/library/MediaLibraryView.vue";

const {
  entries: shows,
  loading,
  refreshing,
  error,
  total,
  statusCounts,
  scoreRanks,
  currentFilters,
  hasMore,
  load,
  loadMore,
  refreshLibrary,
} = useMediaLibraryPage(fetchTVShowsPage, "tv", "TV shows", tvLibraryRevision);

function seasonProgress(show: TVShow): {
  watched: number;
  total: number | null;
} {
  const watched = show.seasons.reduce((sum, s) => sum + s.episodesWatched, 0);
  const known = show.seasons.every((s) => s.episodeCount !== null);
  const total = known
    ? show.seasons.reduce((sum, s) => sum + (s.episodeCount ?? 0), 0)
    : null;
  return { watched, total };
}
// The season currently being watched: the first one not yet fully watched.
function currentSeason(show: TVShow) {
  return show.seasons.find(
    (s) => s.episodeCount === null || s.episodesWatched < s.episodeCount,
  );
}

function toVM(show: TVShow): LibraryCardVM {
  const { watched, total } = seasonProgress(show);
  const season = currentSeason(show) ?? show.seasons.at(-1);
  return {
    id: show.id,
    title: show.title,
    poster: localMediaImage("tv", show.id, "poster", show.posterUrl),
    status: show.status,
    favorite: show.favorite,
    score: show.ratingOverall,
    personalRank: show.personalRank,
    startDate: show.startDate,
    endDate: show.endDate,
    note: show.note,
    genres: show.genres,
    isEpisodic: true,
    episodeEdit: season
      ? {
          id: season.id,
          label:
            "Season " +
            season.seasonNumber +
            (season.name ? ": " + season.name : ""),
          watched: season.episodesWatched,
          total: season.episodeCount,
        }
      : undefined,
    watched,
    total,
    progressLabel: total !== null ? `${watched}/${total}` : `${watched}/–`,
    canAdvance: !!currentSeason(show),
    releaseYear: show.firstAirDate ? show.firstAirDate.slice(0, 4) : null,
    addedAt: Date.parse(show.createdAt) || null,
  };
}

const items = computed(() => shows.value.map(toVM));

onMounted(() => load(currentFilters.value));
useKeptAlive(refreshLibrary, {
  isLoading: () => refreshing.value || loading.value,
  hasError: () => error.value !== null,
});

function findShow(id: string): TVShow {
  const show = shows.value.find((s) => s.id === id);
  if (!show) throw new Error(`TV show ${id} not in the loaded list`);
  return show;
}
function replaceShow(updated: TVShow) {
  const idx = shows.value.findIndex((s) => s.id === updated.id);
  if (idx !== -1) shows.value[idx] = updated;
}

async function onToggleFavorite(id: string) {
  const show = findShow(id);
  const next = !show.favorite;
  show.favorite = next;
  try {
    replaceShow(
      await updateTVShow(id, { ...tvShowToInput(show), favorite: next }),
    );
  } catch {
    show.favorite = !next;
  }
}

async function onSaveNote(id: string, note: string | null) {
  const show = findShow(id);
  replaceShow(await updateTVShow(id, { ...tvShowToInput(show), note }));
}

// No cap on episodes watched — metadata's episode count is often wrong
// or stale, and a rewatch can genuinely outrun it too.
async function onAdvanceEpisode(id: string) {
  const show = findShow(id);
  const season = currentSeason(show);
  if (!season) return;
  const updated = await updateSeason(id, season.id, {
    episodesWatched: season.episodesWatched + 1,
  });
  replaceShow(updated);
  // pressing + on a show that is still Plan to Watch or On Hold means it has
  // been started (or picked up again), so it moves to Watching
  const bucket = statusBucket(updated.status);
  if (bucket === "plan" || bucket === "hold") {
    replaceShow(
      await updateTVShow(id, {
        ...tvShowToInput(updated),
        status: bucketToReal("watching") as TVShowStatus,
      }),
    );
  }
}

async function onSaveEdit(id: string, form: EditForm) {
  const show = findShow(id);
  replaceShow(
    await updateTVShow(id, {
      ...tvShowToInput(show),
      status: resolveStatus(form.status, show.status) as TVShowStatus,
      ratingOverall: form.score,
      ...(form.startDate !== undefined && { startDate: form.startDate }),
      ...(form.endDate !== undefined && { endDate: form.endDate }),
    }),
  );
  // A rating prompt has no episode patch. A quick edit carries only changes
  // to the season that was opened, even if another season is now current.
  const patch = form.episodeEdit;
  if (!patch) return;
  const season = findShow(id).seasons.find((entry) => entry.id === patch.id);
  if (!season) return;
  const seasonUpdates: SeasonUpdateInput = {};
  if (patch.watched !== undefined && patch.watched !== season.episodesWatched) {
    seasonUpdates.episodesWatched = patch.watched;
  }
  if (patch.total !== undefined && patch.total !== season.episodeCount) {
    seasonUpdates.episodeCount = patch.total;
  }
  if (Object.keys(seasonUpdates).length > 0) {
    replaceShow(await updateSeason(id, season.id, seasonUpdates));
  }
}

async function onBulkSetStatus(ids: string[], status: string) {
  for (const id of ids) {
    const show = findShow(id);
    replaceShow(
      await updateTVShow(id, {
        ...tvShowToInput(show),
        status: status as TVShowStatus,
      }),
    );
  }
}
async function onBulkFavorite(ids: string[]) {
  for (const id of ids) {
    const show = findShow(id);
    replaceShow(
      await updateTVShow(id, { ...tvShowToInput(show), favorite: true }),
    );
  }
}
async function onBulkDelete(ids: string[]) {
  for (const id of ids) {
    await deleteTVShow(id);
  }
  shows.value = shows.value.filter((s) => !ids.includes(s.id));
}

function detailRoute(id: string): string {
  return `/tv/${id}`;
}
</script>

<template>
  <MediaLibraryView
    kind="tv"
    add-label="+ Add show"
    :items="items"
    :total="total"
    :status-counts="statusCounts"
    :score-ranks="scoreRanks"
    :has-more="hasMore"
    :loading="loading"
    :error="error"
    :detail-route="detailRoute"
    @filters-change="load"
    @load-more="loadMore"
    @toggle-favorite="onToggleFavorite"
    @advance-episode="onAdvanceEpisode"
    @save-note="onSaveNote"
    @save-edit="onSaveEdit"
    @bulk-set-status="onBulkSetStatus"
    @bulk-favorite="onBulkFavorite"
    @bulk-delete="onBulkDelete"
  />
</template>
