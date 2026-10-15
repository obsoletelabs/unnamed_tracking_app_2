<script setup lang="ts">
import { computed, reactive, ref, watch } from "vue";
import UiModal from "./UiModal.vue";
import type {
  CanonicalMetadata,
  MetadataCandidate,
} from "../services/metadata";
import MetadataComparison from "./MetadataComparison.vue";
import { useMetadataComparison } from "../composables/useMetadataComparison";
import { useMetadataArtwork } from "../composables/useMetadataArtwork";
import type { QuickAddForm } from "../types/mediaLibrary";
import { metadataEpisodeTotal } from "../services/mediaAdd";
import { STATUS_BUCKETS, bucketToReal } from "../utils/mediaStatus";
import { numberOrNull } from "../utils/numberInput";

const props = defineProps<{
  candidate: MetadataCandidate;
  enriching: boolean;
  saving: boolean;
  error: string | null;
  previewArtwork?: (url: string) => Promise<Blob>;
}>();
const emit = defineEmits<{
  close: [];
  save: [form: QuickAddForm, reviewed?: MetadataCandidate];
}>();
type ReviewFields = CanonicalMetadata & {
  poster: string | null;
  backdrop: string | null;
};
const reviewing = ref(false);
const reviewed = ref<MetadataCandidate | undefined>();
const reviewFields = ref<ReviewFields>({ poster: null, backdrop: null });
const artwork = useMetadataArtwork(
  computed(() => (reviewing.value ? props.candidate : null)),
  (url) => {
    if (!props.previewArtwork)
      return Promise.reject(new Error("Artwork preview is unavailable."));
    return props.previewArtwork(url);
  },
);
const comparison = useMetadataComparison(
  () => reviewFields.value,
  () =>
    Object.fromEntries(
      Object.keys({
        ...props.candidate.metadata,
        poster: null,
        backdrop: null,
      }).map((key) => [
        key,
        {
          label:
            (
              {
                poster: "Poster",
                backdrop: "Backdrop",
                seasons: "Offered seasons",
              } as Record<string, string>
            )[key] ??
            key
              .replaceAll("_", " ")
              .replace(/^./, (letter) => letter.toUpperCase()),
          image: key === "poster" || key === "backdrop",
        },
      ]),
    ),
);
watch(
  [() => props.candidate, artwork.urls, reviewing],
  () => {
    if (reviewing.value)
      comparison.offer(
        {
          ...props.candidate.metadata,
          poster: artwork.urls.value.cover,
          backdrop: artwork.urls.value.banner,
        },
        props.candidate.id,
      );
  },
  { deep: true },
);
watch(
  () => props.candidate.id,
  () => {
    reviewing.value = false;
    reviewed.value = undefined;
    comparison.clear();
  },
);
function applyReview() {
  comparison.apply();
  const { poster, backdrop, ...metadata } = reviewFields.value;
  reviewed.value = JSON.parse(
    JSON.stringify({
      ...props.candidate,
      metadata,
      assets: props.candidate.assets.filter(
        (asset) => asset.url === poster || asset.url === backdrop,
      ),
    }),
  );
  reviewing.value = false;
}
function beginReview() {
  const previous = reviewed.value;
  reviewFields.value = {
    ...previous?.metadata,
    poster:
      previous?.assets.find((asset) => asset.kind === "poster")?.url ?? null,
    backdrop:
      previous?.assets.find(
        (asset) => asset.kind === "banner" || asset.kind === "hero",
      )?.url ?? null,
  };
  reviewing.value = true;
}
function cancelReview() {
  reviewing.value = false;
  comparison.clear();
}
const form = reactive<QuickAddForm>({
  status: "plan",
  watched: 0,
  seen: false,
  score: null,
  startDate: null,
  endDate: null,
});
watch(
  () => props.candidate.id,
  () =>
    Object.assign(form, {
      status: "plan",
      watched: 0,
      score: null,
      startDate: null,
      endDate: null,
    }),
);
const total = computed(() => metadataEpisodeTotal(props.candidate));
const score = computed<string | number | null>({
  get: () => form.score,
  set: (value) => {
    try {
      form.score = numberOrNull(value, "Your rating");
    } catch {
      // Keep invalid input invalid so the existing validation blocks submission.
      form.score = NaN;
    }
  },
});
const poster = computed(
  () => props.candidate.assets.find((asset) => asset.kind === "poster")?.url,
);
const invalid = computed(
  () =>
    !Number.isInteger(form.watched) ||
    form.watched < 0 ||
    (total.value != null && form.watched > total.value) ||
    (form.score != null &&
      (!Number.isFinite(form.score) || form.score < 0 || form.score > 10)),
);
function submit() {
  if (invalid.value || props.saving || reviewing.value) return;
  emit(
    "save",
    {
      ...form,
      startDate: form.startDate || null,
      endDate: form.endDate || null,
      status: bucketToReal(form.status),
      watched:
        form.status === "completed" && total.value != null
          ? total.value
          : form.watched,
    },
    reviewed.value,
  );
}
</script>

<template>
  <UiModal title="Add to library" @close="emit('close')">
    <div class="picked-title">
      <img v-if="poster" :src="poster" alt="" />
      <div>
        <h2>{{ candidate.metadata.title || candidate.title }}</h2>
        <p>
          {{
            candidate.media_type === "movie"
              ? "Movie"
              : candidate.media_type === "tv_show"
                ? "TV show"
                : "Anime"
          }}
          · {{ candidate.year ?? "Year unknown" }} ·
          {{ candidate.provider_name }}
        </p>
      </div>
    </div>
    <p v-if="enriching" class="hint" role="status">
      Gathering details and artwork… Available details will be saved if you add
      now.
    </p>
    <p v-if="error" class="ui-error-box" role="alert">{{ error }}</p>
    <button
      v-if="!reviewing"
      type="button"
      class="ui-btn ui-btn-ghost"
      @click="beginReview"
    >
      Review metadata before adding
    </button>
    <p v-if="reviewed && !reviewing" class="hint" role="status">
      Your checked metadata choices are staged. Add to library to save them.
    </p>
    <MetadataComparison
      v-if="reviewing"
      :changes="comparison.changes.value"
      :reason="comparison.reason.value"
      :provider="candidate.provider_name"
      :busy="artwork.busy.value || enriching"
      :previews="artwork.previews.value"
      @toggle="comparison.toggle"
      @apply="applyReview"
      @cancel="cancelReview"
    />
    <div class="fields">
      <label
        ><span>Status</span
        ><select v-model="form.status" aria-label="Status">
          <option
            v-for="status in STATUS_BUCKETS"
            :key="status.key"
            :value="status.key"
          >
            {{ status.label }}
          </option>
        </select></label
      >
      <label v-if="candidate.media_type !== 'movie'"
        ><span>Episodes watched{{ total != null ? ` of ${total}` : "" }}</span
        ><input
          v-model.number="form.watched"
          type="number"
          min="0"
          :max="total ?? undefined"
          step="1"
      /></label>
      <label
        ><span>Your rating (0–10)</span
        ><input v-model="score" type="number" min="0" max="10" step="0.1"
      /></label>
      <label
        ><span>Start date</span><input v-model="form.startDate" type="date"
      /></label>
      <label
        ><span>End date</span><input v-model="form.endDate" type="date"
      /></label>
    </div>
    <p v-if="invalid" class="ui-error-box" role="alert">
      Enter valid episode progress and a rating between 0 and 10.
    </p>
    <template #footer>
      <button
        type="button"
        class="ui-btn ui-btn-ghost"
        :disabled="saving"
        @click="emit('close')"
      >
        Cancel
      </button>
      <button
        type="button"
        class="ui-btn ui-btn-primary"
        :disabled="saving || invalid || reviewing"
        @click="submit"
      >
        {{ saving ? "Adding…" : "Add to library" }}
      </button>
    </template>
  </UiModal>
</template>

<style scoped>
.picked-title {
  display: flex;
  gap: 16px;
  align-items: center;
  margin-bottom: 16px;
}
.picked-title img {
  width: 64px;
  height: 96px;
  object-fit: cover;
  border-radius: var(--ui-radius-control);
}
.picked-title div {
  min-width: 0;
}
h2 {
  margin: 0;
  overflow-wrap: anywhere;
}
.picked-title p,
.hint {
  color: var(--ui-dim);
  font-size: var(--ui-font-small);
  line-height: 1.5;
}
.fields {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(180px, 100%), 1fr));
  gap: 16px;
  margin-top: 16px;
}
label {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
}
input,
select {
  box-sizing: border-box;
  width: 100%;
  min-width: 0;
  padding: 10px;
  border: 1px solid var(--ui-border);
  border-radius: var(--ui-radius-control);
  background: var(--ui-surface);
  color: var(--ui-text);
  font: inherit;
}
</style>
