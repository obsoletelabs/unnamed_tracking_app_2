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
import { useMetadataComparison } from "../composables/useMetadataComparison";
import { useMetadataArtwork } from "../composables/useMetadataArtwork";
import MetadataComparison from "./MetadataComparison.vue";

import { useTitleProtection } from "../utils/titleProtection";
import { numberOrNull } from "../utils/numberInput";

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
    tracking: {
      providerIds: {} as Record<string, string>,
      externalId: null as string | null,
      anilistId: null as string | null,
    },
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
    tracking: {
      providerIds: show.providerIds ?? {},
      externalId: show.externalId,
      anilistId: show.anilistId,
    },
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
    studiosInput: {
      label: "Studios",
      locked: (props.show?.lockedFields ?? []).includes("studios"),
    },
    anilistScore: {
      label: "AniList score",
      locked: (props.show?.lockedFields ?? []).includes("anilist_score"),
    },
    malScore: {
      label: "MAL score",
      locked: (props.show?.lockedFields ?? []).includes("mal_score"),
    },
    tracking: { label: "Provider identity" },
  }),
);
function offerMetadata(result: AnimeMetadataResult) {
  const incoming: Partial<ReturnType<typeof blankFields>> = {};
  incoming.title = result.title ?? undefined;
  incoming.description = result.description ?? undefined;
  incoming.posterUrl = artwork.urls.value.cover;
  incoming.backdropUrl = artwork.urls.value.banner;
  incoming.genresInput = result.genres.join(", ") ?? undefined;
  incoming.firstAirDate = result.firstAirDate ?? undefined;
  incoming.episodeRuntimeMinutes = result.episodeRuntimeMinutes ?? undefined;
  incoming.studiosInput = result.studios.join(", ") ?? undefined;
  incoming.anilistScore = result.anilistScore ?? undefined;
  incoming.malScore = result.malScore ?? undefined;
  incoming.tracking = {
    providerIds: {
      ...fields.value.tracking.providerIds,
      ...result.providerIds,
    },
    externalId: result.providerIds?.mal ?? fields.value.tracking.externalId,
    anilistId: result.providerIds?.anilist ?? fields.value.tracking.anilistId,
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

watch(() => props.show, cancelLookup);

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
      anilistScore: numberOrNull(fields.value.anilistScore, "AniList score"),
      malScore: numberOrNull(fields.value.malScore, "MAL score"),
      externalId: fields.value.tracking.externalId,
      anilistId: fields.value.tracking.anilistId,
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
    :lookup-pending="!!search.selected.value"
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
