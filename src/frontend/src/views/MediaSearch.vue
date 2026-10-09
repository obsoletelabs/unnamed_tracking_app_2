<script setup lang="ts">
import { computed, onDeactivated, ref, watch } from "vue";
import { RouterLink, useRoute } from "vue-router";
import MediaTopBar from "../components/MediaTopBar.vue";
import PageHeader from "../components/PageHeader.vue";
import SegmentedTabs from "../components/SegmentedTabs.vue";
import type { SegmentOption } from "../components/SegmentedTabs.vue";
import MediaAddModal from "../components/MediaAddModal.vue";
import {
  useMediaSearch,
  isMediaSearchType,
} from "../composables/useMediaSearch";
import { addMediaCandidate } from "../services/mediaAdd";
import type { MetadataCandidate } from "../services/metadata";
import type { QuickAddForm } from "../types/mediaLibrary";

const route = useRoute();
const query = ref("");
const media = useMediaSearch(query);
const { results, searching, warnings, selected, enrichingMedia, filter } =
  media;
watch(
  () => route.query.type,
  (type) => {
    filter.value = isMediaSearchType(type) ? type : "all";
  },
  { immediate: true },
);
const OPTIONS: SegmentOption[] = [
  { value: "all", label: "All media" },
  { value: "movie", label: "Movies" },
  { value: "tv_show", label: "TV shows" },
  { value: "anime", label: "Anime" },
];
const saving = ref(false);
const saveError = ref<string | null>(null);
const added = ref<{
  title: string;
  path: string;
  progressWarning: boolean;
} | null>(null);
const noResults = computed(
  () =>
    query.value.trim().length >= 2 && !searching.value && !results.value.length,
);
function typeLabel(candidate: MetadataCandidate) {
  return candidate.media_type === "movie"
    ? "Movie"
    : candidate.media_type === "tv_show"
      ? "TV show"
      : "Anime";
}
async function pick(candidate: MetadataCandidate) {
  saveError.value = null;
  try {
    await media.select(candidate);
  } catch (error) {
    saveError.value =
      error instanceof Error
        ? error.message
        : "Could not load this title's details.";
  }
}
async function save(form: QuickAddForm) {
  if (!selected.value || saving.value) return;
  saving.value = true;
  saveError.value = null;
  try {
    const title = selected.value.metadata.title || selected.value.title;
    const result = await addMediaCandidate(selected.value, form);
    added.value = { title, ...result };
    media.closeSelection();
  } catch (error) {
    saveError.value =
      error instanceof Error ? error.message : "Could not add this title.";
  } finally {
    saving.value = false;
  }
}
onDeactivated(media.stop);
</script>

<template>
  <MediaTopBar active="search" />
  <div class="media-search-page">
    <PageHeader
      title="Add media"
      eyebrow="Your library"
      description="Search movies, TV shows and anime together. Pick a title to add it to the right library."
    />
    <p v-if="added" class="saved-message" role="status">
      Added {{ added.title }}.
      <RouterLink :to="added.path">Open title</RouterLink
      ><span v-if="added.progressWarning">
        Episode progress could not be fully saved. Open the title to update
        it.</span
      >
    </p>
    <label class="search-label"
      ><span>Search media by title</span
      ><input
        v-model="query"
        type="search"
        placeholder="Movie, TV show or anime title"
        @keyup.enter="media.search"
    /></label>
    <SegmentedTabs
      v-model="filter"
      :options="OPTIONS"
      aria-label="Media type"
    />
    <p v-if="searching" class="hint" role="status">
      Searching… Results appear as providers respond.
    </p>
    <p v-else-if="query.trim().length < 2" class="hint">
      Enter at least two characters to search.
    </p>
    <p v-if="noResults" class="hint">
      No matching titles. Try another title or media type.
    </p>
    <ul v-if="warnings.length" class="warnings">
      <li v-for="warning in warnings" :key="warning">{{ warning }}</li>
    </ul>
    <div class="results">
      <button
        v-for="candidate in results"
        :key="`${candidate.media_type}:${candidate.id}`"
        type="button"
        class="result"
        @click="pick(candidate)"
      >
        <span class="result-title">{{
          candidate.metadata.title || candidate.title
        }}</span>
        <span class="result-meta"
          >{{ typeLabel(candidate) }} · {{ candidate.year ?? "Year unknown" }} ·
          {{ candidate.provider_name }}</span
        >
        <span
          v-if="candidate.metadata.description"
          class="result-description"
          >{{ candidate.metadata.description }}</span
        >
        <span class="result-action">Add to library</span>
      </button>
    </div>
    <MediaAddModal
      v-if="selected"
      :candidate="selected"
      :enriching="enrichingMedia"
      :saving="saving"
      :error="saveError"
      @close="!saving && media.closeSelection()"
      @save="save"
    />
  </div>
</template>

<style scoped>
.media-search-page {
  padding: var(--ui-page-padding, 32px);
  max-width: 1200px;
  margin: 0 auto;
}
.search-label {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-bottom: 20px;
}
.search-label input {
  box-sizing: border-box;
  width: 100%;
  min-width: 0;
  padding: 14px;
  border: 1px solid var(--ui-border);
  border-radius: var(--ui-radius-control);
  background: var(--ui-surface);
  color: var(--ui-text);
  font: inherit;
}
.hint,
.result-meta,
.result-description {
  color: var(--ui-dim);
  font-size: var(--ui-font-small);
  line-height: 1.5;
}
.warnings {
  color: var(--ui-error);
  padding-left: 20px;
}
.results {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(300px, 100%), 1fr));
  gap: 12px;
  margin-top: 20px;
}
.result {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 8px;
  padding: 20px;
  text-align: left;
  border: 1px solid var(--ui-border);
  border-radius: var(--ui-radius-card);
  background: var(--ui-surface);
  color: var(--ui-text);
  font: inherit;
  cursor: pointer;
}
.result:hover,
.result:focus-visible {
  border-color: var(--ui-accent-text);
}
.result-title {
  font-weight: var(--ui-weight-title);
  overflow-wrap: anywhere;
}
.result-description {
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.result-action {
  color: var(--ui-accent-text);
  margin-top: auto;
  font-size: var(--ui-font-small);
}
.saved-message {
  border: 1px solid var(--ui-good);
  padding: 12px;
  border-radius: var(--ui-radius-control);
}
@media (max-width: 760px) {
  .media-search-page {
    padding: 20px;
  }
}
</style>
