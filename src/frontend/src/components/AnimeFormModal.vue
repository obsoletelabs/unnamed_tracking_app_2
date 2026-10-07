<script setup lang="ts">
import { ref, computed, watch } from "vue";
import {
  createAnime,
  updateAnime,
  deleteAnime,
  animeToInput,
} from "../services/anime";
import type { AnimeMetadataResult } from "../services/anime";
import type { Anime, AnimeStatus } from "../types/anime";
import {
  STATUS_BUCKETS,
  statusBucket,
  bucketToReal,
} from "../utils/mediaStatus";
import MediaFormShell from "./MediaFormShell.vue";
import MediaMetadataSearch from "./MediaMetadataSearch.vue";
import { useMetadataSearch } from "../utils/useMetadataSearch";
import { splitList } from "../utils/formLists";
import { animeMetadataResult } from "../utils/metadataCandidate";
import { metadataPrefill } from "../utils/metadataPrefill";

import { useTitleProtection } from "../utils/titleProtection";

const props = defineProps<{
  show?: Anime | null;
}>();

const emit = defineEmits<{
  saved: [show: Anime];
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
    studiosInput: "",
    genresInput: "",
    tagsInput: "",
    status: "wishlist" as AnimeStatus,
    favorite: false,
    ratingOverall: null as number | null,
    personalRank: null as number | null,
    posterUrl: null as string | null,
    backdropUrl: null as string | null,
    anilistScore: null as number | null,
    malScore: null as number | null,
    externalId: null as string | null,
    anilistId: null as string | null,
  };
}

const fields = ref(blankFields());
const statusBucketModel = computed({
  get: () => statusBucket(fields.value.status),
  set: (bucket: string) => {
    fields.value.status = bucketToReal(bucket) as AnimeStatus;
  },
});
const { titleProtected, titleLockOverride } = useTitleProtection(
  () => props.show,
  () => fields.value.title,
);

const saving = ref(false);
const deleting = ref(false);
const error = ref<string | null>(null);

const search = useMetadataSearch<AnimeMetadataResult>({
  mediaType: "anime",
  convert: animeMetadataResult,
  noun: "anime",
});

function loadFromShow(show: Anime | null | undefined) {
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
    studiosInput: show.studios.join(", "),
    genresInput: show.genres.join(", "),
    tagsInput: show.tags.join(", "),
    status: show.status,
    favorite: show.favorite,
    ratingOverall: show.ratingOverall,
    personalRank: show.personalRank,
    posterUrl: show.posterUrl,
    backdropUrl: show.backdropUrl,
    anilistScore: show.anilistScore,
    malScore: show.malScore,
    externalId: show.externalId,
    anilistId: show.anilistId,
  };
}

watch(() => props.show, loadFromShow, { immediate: true });

const prefillMetadata = metadataPrefill<ReturnType<typeof blankFields>>();

function applyMetadata(result: AnimeMetadataResult) {
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
  if (!locked.has("studios"))
    incoming.studiosInput = result.studios.join(", ") ?? undefined;
  if (!locked.has("anilist_score"))
    incoming.anilistScore = result.anilistScore ?? undefined;
  if (!locked.has("mal_score"))
    incoming.malScore = result.malScore ?? undefined;
  prefillMetadata(fields.value, incoming, result.candidateId);
  if (result.providerIds)
    fields.value.providerIds = {
      ...fields.value.providerIds,
      ...result.providerIds,
    };
  if (result.malId) fields.value.externalId = result.malId;
  if (result.providerIds?.anilist)
    fields.value.anilistId = result.providerIds.anilist;
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
    detail: [
      result.releaseYear?.toString() ?? result.firstAirDate?.slice(0, 4),
      result.episodeCount ? `${result.episodeCount} ep` : null,
    ]
      .filter(Boolean)
      .join(" · "),
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
      studios: splitList(fields.value.studiosInput),
      genres: splitList(fields.value.genresInput),
      tags: splitList(fields.value.tagsInput),
      status: fields.value.status,
      favorite: fields.value.favorite,
      ratingOverall: fields.value.ratingOverall,
      personalRank: fields.value.personalRank,
      posterUrl: fields.value.posterUrl,
      backdropUrl: fields.value.backdropUrl,
      anilistScore: fields.value.anilistScore,
      malScore: fields.value.malScore,
      externalId: fields.value.externalId,
      anilistId: fields.value.anilistId,
    };
    const saved = props.show
      ? await updateAnime(props.show.id, {
          // keep the fields this form doesn't show
          ...animeToInput(props.show),
          ...input,
        })
      : await createAnime(input);
    emit("saved", saved);
  } catch (e) {
    error.value = e instanceof Error ? e.message : "Failed to save anime.";
  } finally {
    saving.value = false;
  }
}

async function remove() {
  if (!props.show) return;
  deleting.value = true;
  error.value = null;
  try {
    await deleteAnime(props.show.id);
    emit("deleted", props.show.id);
  } catch (e) {
    error.value = e instanceof Error ? e.message : "Failed to delete anime.";
  } finally {
    deleting.value = false;
  }
}
</script>

<template>
  <MediaFormShell
    noun="Anime"
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
      label="Search AniList / MyAnimeList"
      noun="anime"
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
        placeholder="Fullmetal Alchemist: Brotherhood"
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

    <div class="field-row">
      <label class="field">
        <span>AniList score (0-10)</span>
        <input
          v-model.number="fields.anilistScore"
          type="number"
          min="0"
          max="10"
          step="0.1"
          class="text-input"
        />
      </label>
      <label class="field">
        <span>MAL score (0-10)</span>
        <input
          v-model.number="fields.malScore"
          type="number"
          min="0"
          max="10"
          step="0.1"
          class="text-input"
        />
      </label>
    </div>
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
