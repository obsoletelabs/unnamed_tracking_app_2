<script setup lang="ts">
import { ref, computed, watch } from "vue";
import {
  createMovie,
  updateMovie,
  deleteMovie,
  movieToInput,
} from "../services/movies";
import type { MovieMetadataResult } from "../services/movies";
import type { Movie } from "../types/movie";
import {
  formatProgressMinutes,
  parseProgressMinutes,
} from "../utils/watchProgress";
import { statusBucket, statusBucketLabel } from "../utils/mediaStatus";
import {
  MEDIA_EDITOR_TABS,
  MOVIE_PROVIDER_KEYS,
  blankClassification,
  blankTracking,
  classificationFrom,
  classificationToInput,
  trackingFrom,
  trackingToInput,
  type MediaEditorTab,
} from "../utils/mediaEditorFields";
import MediaFormShell from "./MediaFormShell.vue";
import MediaTrackingFields from "./MediaTrackingFields.vue";
import MediaClassificationFields from "./MediaClassificationFields.vue";
import MediaArtworkFields from "./MediaArtworkFields.vue";
import MediaProviderIds from "./MediaProviderIds.vue";
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
    ...blankTracking(),
    ...blankClassification(),
    posterUrl: null as string | null,
    backdropUrl: null as string | null,
    tmdbScore: null as number | null,
  };
}

const fields = ref(blankFields());
const tab = ref<MediaEditorTab>("Basics");
const providerIdsProblem = ref<string | null>(null);
const { titleProtected, titleLockOverride } = useTitleProtection(
  () => props.movie,
  () => fields.value.title,
);

const subtitle = computed(() =>
  [fields.value.releaseDate.slice(0, 4), statusBucketLabel(fields.value.status)]
    .filter(Boolean)
    .join(" · "),
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
    ...trackingFrom(movie),
    ...classificationFrom(movie),
    posterUrl: movie.posterUrl,
    backdropUrl: movie.backdropUrl,
    tmdbScore: movie.tmdbScore,
  };
}

watch(() => props.movie, loadFromMovie, { immediate: true });

const artwork = useMetadataArtwork(search.candidate, search.previewArtwork);
const isLocked = (name: string) =>
  (props.movie?.lockedFields ?? []).includes(name);
const comparison = useMetadataComparison(
  () => fields.value,
  () => ({
    title: { label: "Title", locked: titleProtected.value },
    description: {
      label: "Description",
      locked: isLocked("description"),
    },
    posterUrl: {
      label: "Poster",
      locked: isLocked("poster_url"),
      image: true,
    },
    backdropUrl: {
      label: "Backdrop",
      locked: isLocked("backdrop_url"),
      image: true,
    },
    genresInput: {
      label: "Genres",
      locked: isLocked("genres"),
    },
    releaseDate: {
      label: "Release date",
      locked: isLocked("release_date"),
    },
    runtimeMinutes: {
      label: "Runtime",
      locked: isLocked("runtime_minutes"),
    },
    director: {
      label: "Director",
      locked: isLocked("director"),
    },
    writer: {
      label: "Writer",
      locked: isLocked("writer"),
    },
    countriesInput: { label: "Countries", locked: isLocked("countries") },
    languagesInput: { label: "Languages", locked: isLocked("languages") },
    studiosInput: {
      label: "Studios",
      locked: isLocked("studios"),
    },
    tmdbScore: {
      label: "TMDB score",
      locked: isLocked("tmdb_score"),
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
  incoming.countriesInput = result.countries.join(", ");
  incoming.languagesInput = result.languages.join(", ");
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
    tab.value = "Basics";
    error.value = "Title is required.";
    return;
  }
  if (providerIdsProblem.value) {
    tab.value = "Links";
    error.value = providerIdsProblem.value;
    return;
  }
  const progressMinutes = parseProgressMinutes(fields.value.progressInput);
  if (progressMinutes !== null && Number.isNaN(progressMinutes)) {
    tab.value = "Tracking";
    error.value = "Left off at: enter a valid time, such as 72, 1:12 or 1h12m.";
    return;
  }
  saving.value = true;
  error.value = null;
  try {
    const tracking = trackingToInput(fields.value);
    // a position in a movie you hadn't started means you're watching it
    if (progressMinutes && statusBucket(tracking.status) === "plan") {
      fields.value.status = "in progress";
      tracking.status = "in progress";
    }
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
      ...classificationToInput(fields.value),
      ...tracking,
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
    error.value = e instanceof Error ? e.message : "Could not save the movie.";
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
    error.value =
      e instanceof Error ? e.message : "Could not delete the movie.";
  } finally {
    deleting.value = false;
  }
}
</script>

<template>
  <MediaFormShell
    v-model:tab="tab"
    noun="movie"
    :editing="!!movie"
    :saving="saving"
    :lookup-pending="!!search.selected.value"
    :deleting="deleting"
    :error="error"
    :tabs="MEDIA_EDITOR_TABS"
    :heading="fields.title.trim() || (movie ? movie.title : 'New movie')"
    :subtitle="subtitle"
    :cover="fields.posterUrl"
    @cover="tab = 'Artwork'"
    @closed="emit('closed')"
    @save="submit"
    @delete="remove"
  >
    <template #lookup>
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
    </template>

    <template v-if="tab === 'Basics'">
      <label class="field">
        <span>Title</span>
        <input v-model="fields.title" type="text" placeholder="The Matrix" />
      </label>
      <label v-if="props.movie" class="checkbox-field">
        <input v-model="titleProtected" type="checkbox" />
        <span>Protect title from metadata updates</span>
      </label>
      <MediaTrackingFields :model-value="fields" part="status" noun="movie" />
      <label class="field">
        <span>Description</span>
        <textarea v-model="fields.description" rows="4"></textarea>
      </label>
    </template>

    <template v-else-if="tab === 'Details'">
      <h3 class="editor-section">From the provider</h3>
      <div class="editor-grid">
        <label class="field">
          <span>Release date</span>
          <input v-model="fields.releaseDate" type="date" />
        </label>
        <label class="field">
          <span>Runtime (minutes)</span>
          <input v-model.number="fields.runtimeMinutes" type="number" min="0" />
        </label>
        <label class="field">
          <span>Director</span>
          <input v-model="fields.director" type="text" />
        </label>
        <label class="field">
          <span>Writer</span>
          <input v-model="fields.writer" type="text" />
        </label>
        <label class="field">
          <span>Studios (comma-separated)</span>
          <input v-model="fields.studiosInput" type="text" />
        </label>
        <label class="field">
          <span>TMDB / IMDb score (0-10)</span>
          <input
            v-model.number="fields.tmdbScore"
            type="number"
            min="0"
            max="10"
            step="0.1"
          />
        </label>
      </div>
      <MediaClassificationFields :model-value="fields" />
    </template>

    <MediaTrackingFields
      v-else-if="tab === 'Tracking'"
      :model-value="fields"
      part="journal"
      noun="movie"
    >
      <template #progress>
        <h3 class="editor-section">Progress</h3>
        <label class="field">
          <span>Left off at</span>
          <input
            v-model="fields.progressInput"
            type="text"
            inputmode="text"
            placeholder="72, 1:12 or 1h12m"
          />
        </label>
      </template>
    </MediaTrackingFields>

    <MediaArtworkFields
      v-else-if="tab === 'Artwork'"
      v-model:poster="fields.posterUrl"
      v-model:backdrop="fields.backdropUrl"
      noun="movie"
    />

    <template v-else>
      <h3 class="editor-section">Provider IDs</h3>
      <p class="hint">
        The identities this movie is matched by. A lookup fills them in; edit
        them only to correct a wrong match.
      </p>
      <MediaProviderIds
        :model-value="fields.tracking.providerIds"
        :suggestions="MOVIE_PROVIDER_KEYS"
        @update:model-value="fields.tracking.providerIds = $event"
        @invalid="providerIdsProblem = $event"
      />
    </template>
  </MediaFormShell>
</template>

<style scoped src="../styles/shared/editorForm.css"></style>
