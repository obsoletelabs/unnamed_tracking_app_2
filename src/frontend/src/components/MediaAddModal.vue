<script setup lang="ts">
import { computed, reactive, watch } from "vue";
import UiModal from "./UiModal.vue";
import type { MetadataCandidate } from "../services/metadata";
import type { QuickAddForm } from "../types/mediaLibrary";
import { metadataEpisodeTotal } from "../services/mediaAdd";
import { STATUS_BUCKETS, bucketToReal } from "../utils/mediaStatus";

const props = defineProps<{
  candidate: MetadataCandidate;
  enriching: boolean;
  saving: boolean;
  error: string | null;
}>();
const emit = defineEmits<{ close: []; save: [form: QuickAddForm] }>();
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
    form.score = value === "" || value == null ? null : Number(value);
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
  if (invalid.value || props.saving) return;
  emit("save", {
    ...form,
    startDate: form.startDate || null,
    endDate: form.endDate || null,
    status: bucketToReal(form.status),
    watched:
      form.status === "completed" && total.value != null
        ? total.value
        : form.watched,
  });
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
    <div class="fields">
      <label
        ><span>Status</span
        ><select v-model="form.status">
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
        :disabled="saving || invalid"
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
