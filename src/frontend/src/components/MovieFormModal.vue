<script setup lang="ts">
import { ref, computed, watch } from "vue";
import {
  createMovie,
  updateMovie,
  deleteMovie,
  movieToInput,
} from "../services/movies";
import type { MovieMetadataResult } from "../services/movies";
import type { Movie, MovieStatus } from "../types/movie";
import {
  formatProgressMinutes,
  parseProgressMinutes,
} from "../utils/watchProgress";
import {
  STATUS_BUCKETS,
  statusBucket,
  bucketToReal,
} from "../utils/mediaStatus";
import MediaFormShell from "./MediaFormShell.vue";
import MediaMetadataSearch from "./MediaMetadataSearch.vue";
import { useMetadataSearch } from "../utils/useMetadataSearch";
import { splitList } from "../utils/formLists";
import { movieMetadataResult } from "../utils/metadataCandidate";
import { metadataPrefill } from "../utils/metadataPrefill";

import { useTitleProtection } from "../utils/titleProtection";

const props = defineProps<{
  movie?: Movie | null;
}>();

const emit = defineEmits<{
  saved: [movie: Movie];
  deleted: [movieId: string];
  closed: [];
}>();

function blankFields() {
  return {
    title: "",
    providerIds: {} as Record<string, string>,
    description: "",
    releaseDate: "",
    runtimeMinutes: null as number | null,
    progressInput: "",
    director: "",
    writer: "",
    studiosInput: "",
    genresInput: "",
    tagsInput: "",
    status: "wishlist" as MovieStatus,
    favorite: false,
    ratingOverall: null as number | null,
    personalRank: null as number | null,
    posterUrl: null as string | null,
    backdropUrl: null as string | null,
    tmdbScore: null as number | null,
  };
}

const fields = ref(blankFields());
const statusBucketModel = computed({
  get: () => statusBucket(fields.value.status),
  set: (bucket: string) => {
    fields.value.status = bucketToReal(bucket) as MovieStatus;
  },
});
const { titleProtected, titleLockOverride } = useTitleProtection(
  () => props.movie,
  () => fields.value.title,
);

const saving = ref(false);
const deleting = ref(false);
const error = ref<string | null>(null);

const search = useMetadataSearch<MovieMetadataResult>({
  mediaType: "movie",
  convert: movieMetadataResult,
  noun: "movie",
});

function loadFromMovie(movie: Movie | null | undefined) {
  if (!movie) {
    fields.value = blankFields();
    return;
  }
  fields.value = {
    title: movie.title,
    providerIds: movie.providerIds ?? {},
    description: movie.description ?? "",
    releaseDate: movie.releaseDate ?? "",
    runtimeMinutes: movie.runtimeMinutes,
    progressInput:
      movie.progressMinutes === null
        ? ""
        : formatProgressMinutes(movie.progressMinutes),
    director: movie.director ?? "",
    writer: movie.writer ?? "",
    studiosInput: movie.studios.join(", "),
    genresInput: movie.genres.join(", "),
    tagsInput: movie.tags.join(", "),
    status: movie.status,
    favorite: movie.favorite,
    ratingOverall: movie.ratingOverall,
    personalRank: movie.personalRank,
    posterUrl: movie.posterUrl,
    backdropUrl: movie.backdropUrl,
    tmdbScore: movie.tmdbScore,
  };
}

watch(() => props.movie, loadFromMovie, { immediate: true });

const prefillMetadata = metadataPrefill<ReturnType<typeof blankFields>>();

function applyMetadata(result: MovieMetadataResult) {
  const locked = new Set(props.movie?.lockedFields ?? []);
  const incoming: Partial<ReturnType<typeof blankFields>> = {};
  if (!titleProtected.value) incoming.title = result.title ?? undefined;
  if (!locked.has("description"))
    incoming.description = result.description ?? undefined;
  if (!locked.has("poster_url"))
    incoming.posterUrl = result.posterUrl ?? undefined;
  if (!locked.has("backdrop_url"))
    incoming.backdropUrl = result.backdropUrl ?? undefined;
  if (!locked.has("genres"))
    incoming.genresInput = result.genres.join(", ") ?? undefined;
  if (!locked.has("release_date"))
    incoming.releaseDate = result.releaseDate ?? undefined;
  if (!locked.has("runtime_minutes"))
    incoming.runtimeMinutes = result.runtimeMinutes ?? undefined;
  if (!locked.has("director")) incoming.director = result.director ?? undefined;
  if (!locked.has("writer")) incoming.writer = result.writer ?? undefined;
  if (!locked.has("studios"))
    incoming.studiosInput = result.studios.join(", ") ?? undefined;
  if (!locked.has("tmdb_score"))
    incoming.tmdbScore = result.tmdbScore ?? undefined;
  prefillMetadata(fields.value, incoming, result.candidateId);
  if (result.providerIds)
    fields.value.providerIds = {
      ...fields.value.providerIds,
      ...result.providerIds,
    };
  search.applied(result, [...locked]);
}

watch(search.selected, (result) => {
  if (result) applyMetadata(result);
});

// what the search box lists for each match
const searchResults = computed(() =>
  search.results.value.map((result) => ({
    key: `${result.provider}-${result.providerId}`,
    title: result.title,
    provider: result.provider,
    detail: result.releaseYear?.toString() ?? result.releaseDate?.slice(0, 4),
  })),
);

function pickResult(key: string) {
  const result = search.results.value.find(
    (r) => `${r.provider}-${r.providerId}` === key,
  );
  if (result) void search.select(result).catch(() => {});
}

async function submit() {
  if (!fields.value.title.trim()) {
    error.value = "Title is required.";
    return;
  }
  const progressMinutes = parseProgressMinutes(fields.value.progressInput);
  if (progressMinutes !== null && Number.isNaN(progressMinutes)) {
    error.value = "Left off at: enter minutes (72) or hours:minutes (1:12).";
    return;
  }
  // a position in a movie you hadn't started means you're watching it
  if (progressMinutes && statusBucket(fields.value.status) === "plan") {
    fields.value.status = "in progress";
  }
  saving.value = true;
  error.value = null;
  try {
    const input = {
      progressMinutes: progressMinutes || null,
      title: fields.value.title.trim(),
      providerIds: fields.value.providerIds,
      titleLock: props.movie ? titleLockOverride.value : undefined,
      description: fields.value.description.trim() || null,
      releaseDate: fields.value.releaseDate || null,
      runtimeMinutes: fields.value.runtimeMinutes,
      director: fields.value.director.trim() || null,
      writer: fields.value.writer.trim() || null,
      studios: splitList(fields.value.studiosInput),
      genres: splitList(fields.value.genresInput),
      tags: splitList(fields.value.tagsInput),
      status: fields.value.status,
      favorite: fields.value.favorite,
      ratingOverall: fields.value.ratingOverall,
      personalRank: fields.value.personalRank,
      posterUrl: fields.value.posterUrl,
      backdropUrl: fields.value.backdropUrl,
      tmdbScore: fields.value.tmdbScore,
    };
    const saved = props.movie
      ? await updateMovie(props.movie.id, {
          // keep the fields this form doesn't show
          ...movieToInput(props.movie),
          ...input,
        })
      : await createMovie(input);
    emit("saved", saved);
  } catch (e) {
    error.value = e instanceof Error ? e.message : "Failed to save movie.";
  } finally {
    saving.value = false;
  }
}

async function remove() {
  if (!props.movie) return;
  deleting.value = true;
  error.value = null;
  try {
    await deleteMovie(props.movie.id);
    emit("deleted", props.movie.id);
  } catch (e) {
    error.value = e instanceof Error ? e.message : "Failed to delete movie.";
  } finally {
    deleting.value = false;
  }
}
</script>

<template>
  <MediaFormShell
    noun="Movie"
    :editing="!!movie"
    :saving="saving"
    :deleting="deleting"
    :error="error"
    @closed="emit('closed')"
    @save="submit"
    @delete="remove"
  >
    <MediaMetadataSearch
      v-model:query="search.query.value"
      label="Search metadata providers"
      noun="movie"
      :results="searchResults"
      :searching="search.searching.value"
      :enriching-media="search.enrichingMedia.value"
      :message="search.message.value"
      :warnings="search.warnings.value"
      @search="search.run"
      @pick="pickResult"
    />

    <img
      v-if="fields.posterUrl"
      :src="fields.posterUrl"
      alt=""
      class="poster-preview"
    />

    <label class="field">
      <span>Title</span>
      <input
        v-model="fields.title"
        type="text"
        class="text-input"
        placeholder="The Matrix"
      />
    </label>

    <label v-if="props.movie" class="field checkbox-field">
      <input v-model="titleProtected" type="checkbox" />
      <span>Protect title from metadata updates</span>
    </label>

    <label class="field">
      <span>Description</span>
      <textarea
        v-model="fields.description"
        class="text-input textarea-input"
        rows="3"
      ></textarea>
    </label>

    <div class="field-row">
      <label class="field">
        <span>Release date</span>
        <input v-model="fields.releaseDate" type="date" class="text-input" />
      </label>
      <label class="field">
        <span>Runtime (minutes)</span>
        <input
          v-model.number="fields.runtimeMinutes"
          type="number"
          min="0"
          class="text-input"
        />
      </label>
      <label class="field">
        <span>Left off at (h:mm)</span>
        <input
          v-model="fields.progressInput"
          type="text"
          inputmode="numeric"
          placeholder="not started"
          class="text-input"
        />
      </label>
    </div>

    <div class="field-row">
      <label class="field">
        <span>Director</span>
        <input v-model="fields.director" type="text" class="text-input" />
      </label>
      <label class="field">
        <span>Writer</span>
        <input v-model="fields.writer" type="text" class="text-input" />
      </label>
    </div>

    <label class="field">
      <span>Studios (comma-separated)</span>
      <input v-model="fields.studiosInput" type="text" class="text-input" />
    </label>

    <label class="field">
      <span>Genres (comma-separated)</span>
      <input v-model="fields.genresInput" type="text" class="text-input" />
    </label>

    <label class="field">
      <span>Tags (comma-separated)</span>
      <input v-model="fields.tagsInput" type="text" class="text-input" />
    </label>

    <div class="field-row">
      <label class="field">
        <span>Status</span>
        <select v-model="statusBucketModel" class="text-input">
          <option v-for="s in STATUS_BUCKETS" :key="s.key" :value="s.key">
            {{ s.label }}
          </option>
        </select>
      </label>
      <label class="field">
        <span>Overall rating (0-10)</span>
        <input
          v-model.number="fields.ratingOverall"
          type="number"
          min="0"
          max="10"
          step="0.1"
          class="text-input"
        />
      </label>
    </div>

    <div class="field-row">
      <label class="field checkbox-field">
        <input v-model="fields.favorite" type="checkbox" />
        <span>Favorite</span>
      </label>
      <label class="field">
        <span>Personal rank</span>
        <input
          v-model.number="fields.personalRank"
          type="number"
          min="1"
          class="text-input"
        />
      </label>
    </div>

    <label class="field">
      <span>TMDB / IMDb score (0-10)</span>
      <input
        v-model.number="fields.tmdbScore"
        type="number"
        min="0"
        max="10"
        step="0.1"
        class="text-input"
      />
    </label>
  </MediaFormShell>
</template>

<style scoped src="../styles/shared/mediaForm.css"></style>

<style scoped></style>
