<script setup lang="ts">
import { ref, computed, watch } from "vue";
import {
  createTVShow,
  updateTVShow,
  deleteTVShow,
  tvShowToInput,
} from "../services/tvShows";
import type { TVShowMetadataResult, SeasonInput } from "../services/tvShows";
import type { TVShow, TVShowStatus } from "../types/tv_show";
import {
  STATUS_BUCKETS,
  statusBucket,
  bucketToReal,
} from "../utils/mediaStatus";
import MediaFormShell from "./MediaFormShell.vue";
import MediaMetadataSearch from "./MediaMetadataSearch.vue";
import { useMetadataSearch } from "../utils/useMetadataSearch";
import { splitList } from "../utils/formLists";
import { tvMetadataResult } from "../utils/metadataCandidate";
import { useMetadataComparison } from "../composables/useMetadataComparison";
import { useMetadataArtwork } from "../composables/useMetadataArtwork";
import MetadataComparison from "./MetadataComparison.vue";

import { useTitleProtection } from "../utils/titleProtection";
import { numberOrNull } from "../utils/numberInput";

const props = defineProps<{
  show?: TVShow | null;
}>();

const emit = defineEmits<{
  saved: [show: TVShow];
  deleted: [showId: string];
  closed: [];
}>();

function blankFields() {
  return {
    title: "",
    tracking: {
      providerIds: {} as Record<string, string>,
      externalId: null as string | null,
      seasons: [] as SeasonInput[],
    },
    description: "",
    firstAirDate: "",
    episodeRuntimeMinutes: null as number | null,
    creatorsInput: "",
    genresInput: "",
    tagsInput: "",
    status: "wishlist" as TVShowStatus,
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
    fields.value.status = bucketToReal(bucket) as TVShowStatus;
  },
});
let applySeasons = false;
const stagedSeasons = computed(() => fields.value.tracking.seasons);
const { titleProtected, titleLockOverride } = useTitleProtection(
  () => props.show,
  () => fields.value.title,
);

const saving = ref(false);
const deleting = ref(false);
const error = ref<string | null>(null);

const search = useMetadataSearch<TVShowMetadataResult>({
  mediaType: "tv_show",
  convert: tvMetadataResult,
  noun: "show",
});

function loadFromShow(show: TVShow | null | undefined) {
  applySeasons = false;
  if (!show) {
    fields.value = blankFields();
    return;
  }
  fields.value = {
    title: show.title,
    tracking: {
      providerIds: show.providerIds ?? {},
      externalId: show.externalId,
      seasons: show.seasons.map((season) => ({
        seasonNumber: season.seasonNumber,
        name: season.name,
        episodeCount: season.episodeCount,
        airDate: season.airDate,
        posterUrl: season.posterUrl,
      })),
    },
    description: show.description ?? "",
    firstAirDate: show.firstAirDate ?? "",
    episodeRuntimeMinutes: show.episodeRuntimeMinutes,
    creatorsInput: show.creators.join(", "),
    genresInput: show.genres.join(", "),
    tagsInput: show.tags.join(", "),
    status: show.status,
    favorite: show.favorite,
    ratingOverall: show.ratingOverall,
    personalRank: show.personalRank,
    posterUrl: show.posterUrl,
    backdropUrl: show.backdropUrl,
    tmdbScore: show.tmdbScore,
  };
}

watch(() => props.show, loadFromShow, { immediate: true });

const artwork = useMetadataArtwork(search.candidate, search.previewArtwork);
const comparison = useMetadataComparison(
  () => fields.value,
  () => ({
    title: { label: "Title", locked: titleProtected.value },
    description: {
      label: "Description",
      locked: (props.show?.lockedFields ?? []).includes("description"),
    },
    posterUrl: {
      label: "Poster",
      locked: (props.show?.lockedFields ?? []).includes("poster_url"),
      image: true,
    },
    backdropUrl: {
      label: "Backdrop",
      locked: (props.show?.lockedFields ?? []).includes("backdrop_url"),
      image: true,
    },
    genresInput: {
      label: "Genres",
      locked: (props.show?.lockedFields ?? []).includes("genres"),
    },
    firstAirDate: {
      label: "First air date",
      locked: (props.show?.lockedFields ?? []).includes("first_air_date"),
    },
    episodeRuntimeMinutes: {
      label: "Episode runtime",
      locked: (props.show?.lockedFields ?? []).includes(
        "episode_runtime_minutes",
      ),
    },
    creatorsInput: {
      label: "Creators",
      locked: (props.show?.lockedFields ?? []).includes("creators"),
    },
    tmdbScore: {
      label: "TMDB score",
      locked: (props.show?.lockedFields ?? []).includes("tmdb_score"),
    },
    tracking: { label: "Provider identity and offered seasons" },
  }),
);
function offerMetadata(result: TVShowMetadataResult) {
  const incoming: Partial<ReturnType<typeof blankFields>> = {};
  incoming.title = result.title ?? undefined;
  incoming.description = result.description ?? undefined;
  incoming.posterUrl = artwork.urls.value.cover;
  incoming.backdropUrl = artwork.urls.value.banner;
  incoming.genresInput = result.genres.join(", ") ?? undefined;
  incoming.firstAirDate = result.firstAirDate ?? undefined;
  incoming.episodeRuntimeMinutes = result.episodeRuntimeMinutes ?? undefined;
  incoming.creatorsInput = result.creators.join(", ") ?? undefined;
  incoming.tmdbScore = result.tmdbScore ?? undefined;
  incoming.tracking = {
    providerIds: {
      ...fields.value.tracking.providerIds,
      ...result.providerIds,
    },
    externalId: result.providerIds?.tvmaze ?? fields.value.tracking.externalId,
    seasons: result.seasons.length
      ? result.seasons
      : fields.value.tracking.seasons,
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
  const applied = comparison.apply();
  if (applied.has("tracking")) applySeasons = true;
  search.applied(result, []);
  search.stop();
}

watch(() => props.show, cancelLookup);

// what the search box lists for each match
const searchResults = computed(() =>
  search.results.value.map((result) => ({
    key: `${result.provider}-${result.providerId}`,
    title: result.title,
    provider: result.provider,
    detail: result.releaseYear?.toString() ?? result.firstAirDate?.slice(0, 4),
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
  saving.value = true;
  error.value = null;
  try {
    const input = {
      title: fields.value.title.trim(),
      providerIds: fields.value.tracking.providerIds,
      titleLock: props.show ? titleLockOverride.value : undefined,
      description: fields.value.description.trim() || null,
      firstAirDate: fields.value.firstAirDate || null,
      episodeRuntimeMinutes: numberOrNull(
        fields.value.episodeRuntimeMinutes,
        "Episode runtime",
        { min: 0, integer: true },
      ),
      creators: splitList(fields.value.creatorsInput),
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
      externalId: fields.value.tracking.externalId,
      seasons: !props.show || applySeasons ? stagedSeasons.value : undefined,
    };
    const saved = props.show
      ? await updateTVShow(props.show.id, {
          // keep the fields this form doesn't show
          ...tvShowToInput(props.show),
          ...input,
        })
      : await createTVShow(input);
    emit("saved", saved);
  } catch (e) {
    error.value = e instanceof Error ? e.message : "Failed to save show.";
  } finally {
    saving.value = false;
  }
}

async function remove() {
  if (!props.show) return;
  deleting.value = true;
  error.value = null;
  try {
    await deleteTVShow(props.show.id);
    emit("deleted", props.show.id);
  } catch (e) {
    error.value = e instanceof Error ? e.message : "Failed to delete show.";
  } finally {
    deleting.value = false;
  }
}
</script>

<template>
  <MediaFormShell
    noun="TV Show"
    :editing="!!show"
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
      noun="show"
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
        placeholder="Breaking Bad"
      />
    </label>

    <label v-if="props.show" class="field checkbox-field">
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
        <span>First air date</span>
        <input v-model="fields.firstAirDate" type="date" class="text-input" />
      </label>
      <label class="field">
        <span>Episode runtime (minutes)</span>
        <input
          v-model.number="fields.episodeRuntimeMinutes"
          type="number"
          min="0"
          class="text-input"
        />
      </label>
    </div>

    <label class="field">
      <span>Creators (comma-separated)</span>
      <input v-model="fields.creatorsInput" type="text" class="text-input" />
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

    <p v-if="!show && stagedSeasons.length" class="hint">
      {{ stagedSeasons.length }} season{{
        stagedSeasons.length === 1 ? "" : "s"
      }}
      will be created with this show.
    </p>
  </MediaFormShell>
</template>

<style scoped src="../styles/shared/mediaForm.css"></style>

<style scoped>
.hint {
  font-size: 0.78rem;
  color: var(--ui-dim);
  margin: 0;
}
</style>
