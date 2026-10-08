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
import { metadataPrefill } from "../utils/metadataPrefill";

import { useTitleProtection } from "../utils/titleProtection";

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
    providerIds: {} as Record<string, string>,
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
    externalId: null as string | null,
  };
}

const fields = ref(blankFields());
const statusBucketModel = computed({
  get: () => statusBucket(fields.value.status),
  set: (bucket: string) => {
    fields.value.status = bucketToReal(bucket) as TVShowStatus;
  },
});
// staged from a metadata search result when creating a new show — bulk
// created alongside it so the user doesn't have to type each season in
// by hand. Never used on edit (seasons are managed from the detail page).
const stagedSeasons = ref<SeasonInput[]>([]);
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
  stagedSeasons.value = [];
  if (!show) {
    fields.value = blankFields();
    return;
  }
  fields.value = {
    title: show.title,
    providerIds: show.providerIds ?? {},
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
    externalId: show.externalId,
  };
}

watch(() => props.show, loadFromShow, { immediate: true });

const prefillMetadata = metadataPrefill<ReturnType<typeof blankFields>>();

function applyMetadata(result: TVShowMetadataResult) {
  const locked = new Set(props.show?.lockedFields ?? []);
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
  if (!locked.has("first_air_date"))
    incoming.firstAirDate = result.firstAirDate ?? undefined;
  if (!locked.has("episode_runtime_minutes"))
    incoming.episodeRuntimeMinutes = result.episodeRuntimeMinutes ?? undefined;
  if (!locked.has("creators"))
    incoming.creatorsInput = result.creators.join(", ") ?? undefined;
  if (!locked.has("tmdb_score"))
    incoming.tmdbScore = result.tmdbScore ?? undefined;
  prefillMetadata(fields.value, incoming, result.candidateId);
  if (result.providerIds)
    fields.value.providerIds = {
      ...fields.value.providerIds,
      ...result.providerIds,
    };
  if (result.tvmazeId) fields.value.externalId = result.tvmazeId;
  if (result.seasons.length) stagedSeasons.value = result.seasons;
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
  if (!fields.value.title.trim()) {
    error.value = "Title is required.";
    return;
  }
  saving.value = true;
  error.value = null;
  try {
    const input = {
      title: fields.value.title.trim(),
      providerIds: fields.value.providerIds,
      titleLock: props.show ? titleLockOverride.value : undefined,
      description: fields.value.description.trim() || null,
      firstAirDate: fields.value.firstAirDate || null,
      episodeRuntimeMinutes: fields.value.episodeRuntimeMinutes,
      creators: splitList(fields.value.creatorsInput),
      genres: splitList(fields.value.genresInput),
      tags: splitList(fields.value.tagsInput),
      status: fields.value.status,
      favorite: fields.value.favorite,
      ratingOverall: fields.value.ratingOverall,
      personalRank: fields.value.personalRank,
      posterUrl: fields.value.posterUrl,
      backdropUrl: fields.value.backdropUrl,
      tmdbScore: fields.value.tmdbScore,
      externalId: fields.value.externalId,
      seasons: props.show ? undefined : stagedSeasons.value,
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
