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
import { useMetadataComparison } from "../composables/useMetadataComparison";
import { useMetadataArtwork } from "../composables/useMetadataArtwork";
import MetadataComparison from "./MetadataComparison.vue";

import { useTitleProtection } from "../utils/titleProtection";
import { numberOrNull } from "../utils/numberInput";

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
    tracking: { providerIds: {} as Record<string, string> },
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
    tracking: { providerIds: movie.providerIds ?? {} },
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

const artwork = useMetadataArtwork(search.candidate, search.previewArtwork);
const comparison = useMetadataComparison(
  () => fields.value,
  () => ({
    title: { label: "Title", locked: titleProtected.value },
    description: {
      label: "Description",
      locked: (props.movie?.lockedFields ?? []).includes("description"),
    },
    posterUrl: {
      label: "Poster",
      locked: (props.movie?.lockedFields ?? []).includes("poster_url"),
      image: true,
    },
    backdropUrl: {
      label: "Backdrop",
      locked: (props.movie?.lockedFields ?? []).includes("backdrop_url"),
      image: true,
    },
    genresInput: {
      label: "Genres",
      locked: (props.movie?.lockedFields ?? []).includes("genres"),
    },
    releaseDate: {
      label: "Release date",
      locked: (props.movie?.lockedFields ?? []).includes("release_date"),
    },
    runtimeMinutes: {
      label: "Runtime",
      locked: (props.movie?.lockedFields ?? []).includes("runtime_minutes"),
    },
    director: {
      label: "Director",
      locked: (props.movie?.lockedFields ?? []).includes("director"),
    },
    writer: {
      label: "Writer",
      locked: (props.movie?.lockedFields ?? []).includes("writer"),
    },
    studiosInput: {
      label: "Studios",
      locked: (props.movie?.lockedFields ?? []).includes("studios"),
    },
    tmdbScore: {
      label: "TMDB score",
      locked: (props.movie?.lockedFields ?? []).includes("tmdb_score"),
    },
    tracking: { label: "Provider identity" },
  }),
);
function offerMetadata(result: MovieMetadataResult) {
  const incoming: Partial<ReturnType<typeof blankFields>> = {};
  incoming.title = result.title ?? undefined;
  incoming.description = result.description ?? undefined;
  incoming.posterUrl = artwork.urls.value.cover;
  incoming.backdropUrl = artwork.urls.value.banner;
  incoming.genresInput = result.genres.join(", ") ?? undefined;
  incoming.releaseDate = result.releaseDate ?? undefined;
  incoming.runtimeMinutes = result.runtimeMinutes ?? undefined;
  incoming.director = result.director ?? undefined;
  incoming.writer = result.writer ?? undefined;
  incoming.studiosInput = result.studios.join(", ") ?? undefined;
  incoming.tmdbScore = result.tmdbScore ?? undefined;
  incoming.tracking = {
    providerIds: {
      ...fields.value.tracking.providerIds,
      ...result.providerIds,
    },
  };
  comparison.offer(
    incoming,
    result.candidateId ?? `${result.provider}-${result.providerId}`,
  );
}
watch([search.selected, artwork.urls], ([result]) => {
  if (result) offerMetadata(result);
  else comparison.clear();
});
function cancelLookup() {
  comparison.clear();
  search.stop();
}
function applyLookup() {
  const result = search.selected.value;
  if (!result) return;
  comparison.apply();
  search.applied(result, []);
  search.stop();
}

watch(() => props.movie, cancelLookup);

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
  if (search.selected.value) return;
  if (!fields.value.title.trim()) {
    error.value = "Title is required.";
    return;
  }
  const progressMinutes = parseProgressMinutes(fields.value.progressInput);
  if (progressMinutes !== null && Number.isNaN(progressMinutes)) {
    error.value = "Left off at: enter a valid time, such as 72, 1:12 or 1h12m.";
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
      progressMinutes,
      title: fields.value.title.trim(),
      providerIds: fields.value.tracking.providerIds,
      titleLock: props.movie ? titleLockOverride.value : undefined,
      description: fields.value.description.trim() || null,
      releaseDate: fields.value.releaseDate || null,
      runtimeMinutes: numberOrNull(fields.value.runtimeMinutes, "Runtime", {
        min: 0,
        integer: true,
      }),
      director: fields.value.director.trim() || null,
      writer: fields.value.writer.trim() || null,
      studios: splitList(fields.value.studiosInput),
      genres: splitList(fields.value.genresInput),
      tags: splitList(fields.value.tagsInput),
      status: fields.value.status,
      favorite: fields.value.favorite,
      ratingOverall: numberOrNull(fields.value.ratingOverall, "Your rating", {
        min: 0,
        max: 10,
      }),
      personalRank: numberOrNull(fields.value.personalRank, "Personal rank"),
      posterUrl: fields.value.posterUrl,
      backdropUrl: fields.value.backdropUrl,
      tmdbScore: numberOrNull(fields.value.tmdbScore, "TMDB score", {
        min: 0,
        max: 10,
      }),
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
    :lookup-pending="!!search.selected.value"
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

    <MetadataComparison
      v-if="search.selected.value"
      :changes="comparison.changes.value"
      :reason="comparison.reason.value"
      :provider="search.selected.value.provider"
      :busy="artwork.busy.value || search.enrichingMedia.value"
      :previews="artwork.previews.value"
      @toggle="comparison.toggle"
      @apply="applyLookup"
      @cancel="cancelLookup"
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
        <span>Left off at</span>
        <input
          v-model="fields.progressInput"
          type="text"
          inputmode="text"
          placeholder="72, 1:12 or 1h12m"
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
