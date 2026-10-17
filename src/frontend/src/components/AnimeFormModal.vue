<script setup lang="ts">
import { ref, computed, watch } from "vue";
import {
  createAnime,
  updateAnime,
  deleteAnime,
  animeToInput,
} from "../services/anime";
import type { AnimeMetadataResult } from "../services/anime";
import type { Anime } from "../types/anime";
import { statusBucketLabel } from "../utils/mediaStatus";
import {
  ANIME_PROVIDER_KEYS,
  MEDIA_EDITOR_TABS,
  blankClassification,
  blankTracking,
  classificationFrom,
  classificationToInput,
  followProviderId,
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
    format: "",
    ...blankTracking(),
    ...blankClassification(),
    posterUrl: null as string | null,
    backdropUrl: null as string | null,
    anilistScore: null as number | null,
    malScore: null as number | null,
  };
}

const fields = ref(blankFields());
const tab = ref<MediaEditorTab>("Basics");
const providerIdsProblem = ref<string | null>(null);
const { titleProtected, titleLockOverride } = useTitleProtection(
  () => props.show,
  () => fields.value.title,
);

const FORMATS = ["TV", "TV Short", "Movie", "Special", "OVA", "ONA", "Music"];
const subtitle = computed(() =>
  [
    fields.value.firstAirDate.slice(0, 4),
    statusBucketLabel(fields.value.status),
  ]
    .filter(Boolean)
    .join(" · "),
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
    format: show.format ?? "",
    ...trackingFrom(show),
    ...classificationFrom(show),
    posterUrl: show.posterUrl,
    backdropUrl: show.backdropUrl,
    anilistScore: show.anilistScore,
    malScore: show.malScore,
  };
}

watch(() => props.show, loadFromShow, { immediate: true });

const artwork = useMetadataArtwork(search.candidate, search.previewArtwork);
const isLocked = (name: string) =>
  (props.show?.lockedFields ?? []).includes(name);
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
    firstAirDate: {
      label: "First air date",
      locked: isLocked("first_air_date"),
    },
    episodeRuntimeMinutes: {
      label: "Episode runtime",
      locked: isLocked("episode_runtime_minutes"),
    },
    countriesInput: { label: "Countries", locked: isLocked("countries") },
    format: { label: "Format", locked: isLocked("format") },
    studiosInput: {
      label: "Studios",
      locked: isLocked("studios"),
    },
    anilistScore: {
      label: "AniList score",
      locked: isLocked("anilist_score"),
    },
    malScore: {
      label: "MAL score",
      locked: isLocked("mal_score"),
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
  incoming.countriesInput = result.countries.join(", ");
  incoming.format = result.format ?? undefined;
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
    tab.value = "Basics";
    error.value = "Title is required.";
    return;
  }
  if (providerIdsProblem.value) {
    tab.value = "Links";
    error.value = providerIdsProblem.value;
    return;
  }
  const format = fields.value.format.trim();
  if (format.length > 20) {
    tab.value = "Details";
    error.value = "Format: use 20 characters or fewer.";
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
      format: format || null,
      ...classificationToInput(fields.value),
      ...trackingToInput(fields.value),
      posterUrl: fields.value.posterUrl,
      backdropUrl: fields.value.backdropUrl,
      anilistScore: numberOrNull(fields.value.anilistScore, "AniList score"),
      malScore: numberOrNull(fields.value.malScore, "MAL score"),
      // MyAnimeList's and AniList's ids also live in their own columns;
      // follow an edited identity
      externalId: followProviderId(
        fields.value.tracking.externalId,
        props.show?.providerIds?.mal,
        fields.value.tracking.providerIds.mal,
      ),
      anilistId: followProviderId(
        fields.value.tracking.anilistId,
        props.show?.providerIds?.anilist,
        fields.value.tracking.providerIds.anilist,
      ),
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
    v-model:tab="tab"
    noun="Anime"
    :editing="!!show"
    :saving="saving"
    :lookup-pending="!!search.selected.value"
    :deleting="deleting"
    :error="error"
    :tabs="MEDIA_EDITOR_TABS"
    :heading="fields.title.trim() || (show ? show.title : 'New anime')"
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
    </template>

    <template v-if="tab === 'Basics'">
      <label class="field">
        <span>Title</span>
        <input
          v-model="fields.title"
          type="text"
          placeholder="Fullmetal Alchemist: Brotherhood"
        />
      </label>
      <label v-if="props.show" class="checkbox-field">
        <input v-model="titleProtected" type="checkbox" />
        <span>Protect title from metadata updates</span>
      </label>
      <MediaTrackingFields :model-value="fields" part="status" noun="anime" />
      <label class="field">
        <span>Description</span>
        <textarea v-model="fields.description" rows="4"></textarea>
      </label>
    </template>

    <template v-else-if="tab === 'Details'">
      <h3 class="editor-section">From the provider</h3>
      <div class="editor-grid">
        <label class="field">
          <span>First air date</span>
          <input v-model="fields.firstAirDate" type="date" />
        </label>
        <label class="field">
          <span>Episode runtime (minutes)</span>
          <input
            v-model.number="fields.episodeRuntimeMinutes"
            type="number"
            min="0"
          />
        </label>
        <label class="field">
          <span>Studios (comma-separated)</span>
          <input v-model="fields.studiosInput" type="text" />
        </label>
        <label class="field">
          <span>AniList score (0-10)</span>
          <input
            v-model.number="fields.anilistScore"
            type="number"
            min="0"
            max="10"
            step="0.1"
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
          />
        </label>
      </div>
      <MediaClassificationFields :model-value="fields">
        <label class="field">
          <span>Format</span>
          <input
            v-model="fields.format"
            type="text"
            list="anime-formats"
            maxlength="20"
            placeholder="TV, Movie, OVA…"
          />
          <datalist id="anime-formats">
            <option v-for="name in FORMATS" :key="name" :value="name" />
          </datalist>
        </label>
      </MediaClassificationFields>
    </template>

    <MediaTrackingFields
      v-else-if="tab === 'Tracking'"
      :model-value="fields"
      part="journal"
      noun="anime"
    >
      <template #progress>
        <p class="hint">
          Season and episode progress is tracked on the anime's page.
        </p>
      </template>
    </MediaTrackingFields>

    <MediaArtworkFields
      v-else-if="tab === 'Artwork'"
      v-model:poster="fields.posterUrl"
      v-model:backdrop="fields.backdropUrl"
      noun="anime"
    />

    <template v-else>
      <h3 class="editor-section">Provider IDs</h3>
      <p class="hint">
        The identities this anime is matched by. A lookup fills them in; edit
        them only to correct a wrong match.
      </p>
      <MediaProviderIds
        :model-value="fields.tracking.providerIds"
        :suggestions="ANIME_PROVIDER_KEYS"
        @update:model-value="fields.tracking.providerIds = $event"
        @invalid="providerIdsProblem = $event"
      />
    </template>
  </MediaFormShell>
</template>

<style scoped src="../styles/shared/editorForm.css"></style>
