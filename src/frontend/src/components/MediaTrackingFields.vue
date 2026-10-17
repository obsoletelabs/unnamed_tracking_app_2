<script setup lang="ts">
import { computed } from "vue";
import {
  STATUS_BUCKETS,
  statusBucket,
  bucketToReal,
} from "../utils/mediaStatus";
import type { MediaStatus, TrackingFields } from "../utils/mediaEditorFields";

// Your own values for a movie, show or anime, shared by the three editors:
// "status" is the Basics section, "journal" the Tracking tab. The editor
// owns the state and validates it on save with trackingToInput().
defineProps<{
  part: "status" | "journal";
  // the media's name in labels, e.g. "movie" or "show"
  noun: string;
}>();
const fields = defineModel<TrackingFields>({ required: true });

const statusBucketModel = computed({
  get: () => statusBucket(fields.value.status),
  set: (bucket: string) => {
    fields.value.status = bucketToReal(bucket) as MediaStatus;
  },
});
</script>

<template>
  <template v-if="part === 'status'">
    <div class="editor-grid">
      <label class="field">
        <span>Status</span>
        <select v-model="statusBucketModel">
          <option v-for="s in STATUS_BUCKETS" :key="s.key" :value="s.key">
            {{ s.label }}
          </option>
        </select>
      </label>
      <label class="field checkbox-field">
        <input v-model="fields.favorite" type="checkbox" />
        <span>Favorite</span>
      </label>
    </div>
  </template>

  <template v-else>
    <slot name="progress" />
    <h3 class="editor-section">History</h3>
    <div class="editor-grid">
      <label class="field">
        <span>Started</span>
        <input v-model="fields.startDate" type="date" />
      </label>
      <label class="field">
        <span>Finished</span>
        <input v-model="fields.endDate" type="date" :min="fields.startDate" />
      </label>
      <label class="field">
        <span>Rewatches</span>
        <input
          v-model.number="fields.rewatches"
          type="number"
          min="0"
          step="1"
        />
      </label>
      <label class="field">
        <span>Source (where you watch it)</span>
        <input
          v-model="fields.source"
          type="text"
          maxlength="50"
          placeholder="Netflix, Blu-ray…"
        />
      </label>
    </div>

    <h3 class="editor-section">Your ratings</h3>
    <div class="editor-grid">
      <label class="field">
        <span>Overall (0-10)</span>
        <input
          v-model.number="fields.ratingOverall"
          type="number"
          min="0"
          max="10"
          step="0.1"
        />
      </label>
      <label class="field">
        <span>Personal rank</span>
        <input v-model.number="fields.personalRank" type="number" min="1" />
      </label>
      <label class="field">
        <span>Story (0-10)</span>
        <input
          v-model.number="fields.ratingStory"
          type="number"
          min="0"
          max="10"
          step="0.1"
        />
      </label>
      <label class="field">
        <span>Performance (0-10)</span>
        <input
          v-model.number="fields.ratingPerformance"
          type="number"
          min="0"
          max="10"
          step="0.1"
        />
      </label>
      <label class="field">
        <span>Soundtrack (0-10)</span>
        <input
          v-model.number="fields.ratingSoundtrack"
          type="number"
          min="0"
          max="10"
          step="0.1"
        />
      </label>
    </div>

    <h3 class="editor-section">Tags and notes</h3>
    <label class="field">
      <span>Tags (comma-separated)</span>
      <input v-model="fields.tagsInput" type="text" />
    </label>
    <label class="field">
      <span>Notes about this {{ noun }}</span>
      <textarea v-model="fields.note" rows="4"></textarea>
    </label>
  </template>
</template>

<style scoped src="../styles/shared/editorForm.css"></style>
