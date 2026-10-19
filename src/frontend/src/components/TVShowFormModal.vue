<script setup lang="ts">
import { ref, computed, watch } from "vue";
import {
  createTVShow,
  updateTVShow,
  deleteTVShow,
  tvShowToInput,
} from "../services/tvShows";
import type { TVShowMetadataResult, SeasonInput } from "../services/tvShows";
import type { TVShow } from "../types/tv_show";
import { statusBucketLabel } from "../utils/mediaStatus";
import {
  MEDIA_EDITOR_TABS,
  TV_PROVIDER_KEYS,
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
let applySeasons = false;
const stagedSeasons = computed(() => fields.value.tracking.seasons);
const { titleProtected, titleLockOverride } = useTitleProtection(
  () => props.show,
  () => fields.value.title,
);

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
    ...trackingFrom(show),
    ...classificationFrom(show),
    posterUrl: show.posterUrl,
    backdropUrl: show.backdropUrl,
    tmdbScore: show.tmdbScore,
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
    languagesInput: { label: "Languages", locked: isLocked("languages") },
    creatorsInput: {
      label: "Creators",
      locked: isLocked("creators"),
    },
    tmdbScore: {
      label: "TMDB score",
      locked: isLocked("tmdb_score"),
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
  incoming.countriesInput = result.countries.join(", ");
  incoming.languagesInput = result.languages.join(", ");
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
    tab.value = "Basics";
    error.value = "Title is required.";
    return;
  }
  if (providerIdsProblem.value) {
    tab.value = "Links";
    error.value = providerIdsProblem.value;
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
      ...classificationToInput(fields.value),
      ...trackingToInput(fields.value),
      posterUrl: fields.value.posterUrl,
      backdropUrl: fields.value.backdropUrl,
      tmdbScore: numberOrNull(fields.value.tmdbScore, "TMDB score", {
        min: 0,
        max: 10,
      }),
      // TVmaze's id also lives in its own column; follow an edited identity
      externalId: followProviderId(
        fields.value.tracking.externalId,
        props.show?.providerIds?.tvmaze,
        fields.value.tracking.providerIds.tvmaze,
      ),
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
    error.value = e instanceof Error ? e.message : "Could not save the show.";
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
    error.value = e instanceof Error ? e.message : "Could not delete the show.";
  } finally {
    deleting.value = false;
  }
}
</script>

<template>
  <MediaFormShell
    v-model:tab="tab"
    noun="TV show"
    :editing="!!show"
    :saving="saving"
    :lookup-pending="!!search.selected.value"
    :deleting="deleting"
    :error="error"
    :tabs="MEDIA_EDITOR_TABS"
    :heading="fields.title.trim() || (show ? show.title : 'New show')"
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
        media-type="tv_show"
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
    </template>

    <template v-if="tab === 'Basics'">
      <label class="field">
        <span>Title</span>
        <input v-model="fields.title" type="text" placeholder="Breaking Bad" />
      </label>
      <label v-if="props.show" class="checkbox-field">
        <input v-model="titleProtected" type="checkbox" />
        <span>Protect title from metadata updates</span>
      </label>
      <MediaTrackingFields :model-value="fields" part="status" noun="show" />
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
          <span>Creators (comma-separated)</span>
          <input v-model="fields.creatorsInput" type="text" />
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
      noun="show"
    >
      <template #progress>
        <p class="hint">
          Season and episode progress is tracked on the show's page.
        </p>
      </template>
    </MediaTrackingFields>

    <MediaArtworkFields
      v-else-if="tab === 'Artwork'"
      v-model:poster="fields.posterUrl"
      v-model:backdrop="fields.backdropUrl"
      noun="show"
    />

    <template v-else>
      <h3 class="editor-section">Provider IDs</h3>
      <p class="hint">
        The identities this show is matched by. A lookup fills them in; edit
        them only to correct a wrong match.
      </p>
      <MediaProviderIds
        :model-value="fields.tracking.providerIds"
        :suggestions="TV_PROVIDER_KEYS"
        @update:model-value="fields.tracking.providerIds = $event"
        @invalid="providerIdsProblem = $event"
      />
      <p v-if="!show && stagedSeasons.length" class="hint">
        {{ stagedSeasons.length }} season{{
          stagedSeasons.length === 1 ? "" : "s"
        }}
        will be created with this show.
      </p>
    </template>
  </MediaFormShell>
</template>

<style scoped src="../styles/shared/editorForm.css"></style>
