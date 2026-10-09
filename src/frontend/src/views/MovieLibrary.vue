<script setup lang="ts">
import { ref, computed, onMounted } from "vue";
import { useKeptAlive } from "../utils/useKeptAlive";
import type { LibraryFilters } from "../utils/libraryFilters";
import {
  fetchMoviesPage,
  updateMovie,
  deleteMovie,
  movieToInput,
} from "../services/movies";
import type { Movie, MovieStatus } from "../types/movie";
import { localMediaImage } from "../utils/mediaImages";
import MediaLibraryView from "../components/library/MediaLibraryView.vue";
import type {
  LibraryCardVM,
  EditForm,
} from "../components/library/MediaLibraryView.vue";

const movies = ref<Movie[]>([]);
const loading = ref(true);
const error = ref<string | null>(null);

const COMPLETED_STATUSES: MovieStatus[] = ["watched", "favorite", "rewatch"];

function formatRuntime(minutes: number | null): string {
  if (!minutes) return "–";
  const hrs = Math.floor(minutes / 60);
  const mins = minutes % 60;
  return hrs > 0 ? `${hrs}h ${mins}m` : `${mins}m`;
}

function toVM(m: Movie): LibraryCardVM {
  const seen = COMPLETED_STATUSES.includes(m.status);
  return {
    id: m.id,
    title: m.title,
    poster: localMediaImage("movie", m.id, "poster", m.posterUrl),
    status: m.status,
    favorite: m.favorite,
    score: m.ratingOverall,
    personalRank: m.personalRank,
    note: m.note,
    genres: m.genres,
    isEpisodic: false,
    watched: seen ? 1 : 0,
    total: 1,
    progressLabel: formatRuntime(m.runtimeMinutes),
    canAdvance: false,
    releaseYear: m.releaseDate ? m.releaseDate.slice(0, 4) : null,
    addedAt: Date.parse(m.createdAt) || null,
  };
}

const items = computed(() => movies.value.map(toVM));

// Only the very first load shows the loading state; a refresh when the
// page comes back swaps data in quietly, so titles never blink away.
let loadRequest = 0;
const refreshing = ref(false);
const total = ref(0);
const statusCounts = ref<Record<string, number>>({});
const scoreRanks = ref<Record<string, number>>({});
const pageSize = 100;
const currentSearch = ref("");
const currentFilters = ref<LibraryFilters & { statusBucket: string }>({
  search: "",
  genres: [],
  genreMatchAll: false,
  formats: [],
  onlyFavorites: false,
  onlyUnrated: false,
  onlyWithNote: false,
  minScore: null,
  yearFrom: "",
  yearTo: "",
  statusBucket: "all",
});
async function load(
  filters: LibraryFilters & { statusBucket: string } = currentFilters.value,
) {
  currentFilters.value = filters;
  currentSearch.value = filters.search;
  const request = ++loadRequest;
  refreshing.value = true;
  if (!movies.value.length) loading.value = true;
  try {
    const page = await fetchMoviesPage(0, pageSize, filters);
    if (request !== loadRequest) return;
    movies.value = page.items;
    total.value = page.total;
    statusCounts.value = page.statusCounts;
    scoreRanks.value = page.scoreRanks;
    error.value = null;
  } catch (e) {
    if (request !== loadRequest) return;
    error.value = e instanceof Error ? e.message : "Failed to load movies.";
  } finally {
    if (request === loadRequest) {
      loading.value = false;
      refreshing.value = false;
    }
  }
}
async function loadMore() {
  if (loading.value || movies.value.length >= total.value) return;
  const request = loadRequest;
  loading.value = true;
  try {
    const page = await fetchMoviesPage(
      movies.value.length,
      pageSize,
      currentFilters.value,
    );
    if (request !== loadRequest) return;
    movies.value.push(...page.items);
    error.value = null;
  } catch (e) {
    if (request !== loadRequest) return;
    error.value =
      e instanceof Error ? e.message : "Failed to load more movies.";
  } finally {
    if (request === loadRequest) loading.value = false;
  }
}
onMounted(load);
useKeptAlive(load, {
  isLoading: () => refreshing.value || loading.value,
  hasError: () => error.value !== null,
});

function findMovie(id: string): Movie {
  const movie = movies.value.find((m) => m.id === id);
  if (!movie) throw new Error(`Movie ${id} not in the loaded list`);
  return movie;
}
function replaceMovie(updated: Movie) {
  const idx = movies.value.findIndex((m) => m.id === updated.id);
  if (idx !== -1) movies.value[idx] = updated;
}

async function onToggleFavorite(id: string) {
  const movie = findMovie(id);
  const next = !movie.favorite;
  movie.favorite = next;
  try {
    replaceMovie(
      await updateMovie(id, { ...movieToInput(movie), favorite: next }),
    );
  } catch {
    movie.favorite = !next;
  }
}

async function onSaveNote(id: string, note: string | null) {
  const movie = findMovie(id);
  replaceMovie(await updateMovie(id, { ...movieToInput(movie), note }));
}

async function onSaveEdit(id: string, form: EditForm) {
  const movie = findMovie(id);
  replaceMovie(
    await updateMovie(id, {
      ...movieToInput(movie),
      status: form.status as MovieStatus,
      ratingOverall: form.score,
    }),
  );
}

async function onBulkSetStatus(ids: string[], status: string) {
  for (const id of ids) {
    const movie = findMovie(id);
    replaceMovie(
      await updateMovie(id, {
        ...movieToInput(movie),
        status: status as MovieStatus,
      }),
    );
  }
}
async function onBulkFavorite(ids: string[]) {
  for (const id of ids) {
    const movie = findMovie(id);
    replaceMovie(
      await updateMovie(id, { ...movieToInput(movie), favorite: true }),
    );
  }
}
async function onBulkDelete(ids: string[]) {
  for (const id of ids) {
    await deleteMovie(id);
  }
  movies.value = movies.value.filter((m) => !ids.includes(m.id));
}

function detailRoute(id: string): string {
  return `/movies/${id}`;
}
</script>

<template>
  <MediaLibraryView
    kind="movie"
    add-label="+ Add Movie"
    :items="items"
    :total="total"
    :status-counts="statusCounts"
    :score-ranks="scoreRanks"
    :loading="loading"
    :error="error"
    :detail-route="detailRoute"
    @filters-change="load"
    @load-more="loadMore"
    @toggle-favorite="onToggleFavorite"
    @save-note="onSaveNote"
    @save-edit="onSaveEdit"
    @bulk-set-status="onBulkSetStatus"
    @bulk-favorite="onBulkFavorite"
    @bulk-delete="onBulkDelete"
  />
</template>
