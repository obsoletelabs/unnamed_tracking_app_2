<script setup lang="ts">
import { ref, watch } from "vue";
import {
  providerIdRows,
  providerIdsFromRows,
  type ProviderIdRow,
} from "../utils/mediaEditorFields";

// The provider identities a movie, show or anime is matched by (for example
// tmdb, imdb, tvmaze, anilist). They travel in the media's own request as
// provider_ids; the API accepts lowercase names up to 50 characters, values
// up to 256 and at most 32 entries. Problems are shown here and also reported
// through `invalid` so the editor can refuse to save.
defineProps<{ suggestions: readonly string[] }>();
const ids = defineModel<Record<string, string>>({ required: true });
const emit = defineEmits<{ invalid: [message: string | null] }>();

const rows = ref<ProviderIdRow[]>(providerIdRows(ids.value));
const problem = ref<string | null>(null);

// Identities offered by a confirmed lookup replace the rows; our own edits
// already match, so they are not echoed back.
watch(ids, (next) => {
  const own = providerIdsFromRows(rows.value).ids;
  const same =
    Object.keys(next).length === Object.keys(own).length &&
    Object.entries(next).every(([key, value]) => own[key] === value);
  if (!same) {
    rows.value = providerIdRows(next);
    problem.value = null;
    emit("invalid", null);
  }
});

function changed() {
  const { ids: next, error } = providerIdsFromRows(rows.value);
  problem.value = error;
  emit("invalid", error);
  ids.value = next;
}
function add() {
  rows.value.push({ key: "", value: "" });
}
function remove(index: number) {
  rows.value.splice(index, 1);
  changed();
}
const listId = `provider-keys-${Math.random().toString(36).slice(2, 8)}`;
</script>

<template>
  <div class="provider-ids">
    <datalist :id="listId">
      <option v-for="key in suggestions" :key="key" :value="key" />
    </datalist>
    <p v-if="!rows.length" class="hint">
      No provider IDs yet. A confirmed metadata lookup fills these in.
    </p>
    <div v-for="(row, index) in rows" :key="index" class="provider-row">
      <label class="field">
        <span>Provider</span>
        <input
          v-model="row.key"
          type="text"
          :list="listId"
          maxlength="50"
          autocapitalize="off"
          @input="changed"
        />
      </label>
      <label class="field">
        <span>ID</span>
        <input
          v-model="row.value"
          type="text"
          maxlength="256"
          @input="changed"
        />
      </label>
      <button
        type="button"
        class="secondary-button"
        :aria-label="`Remove provider ID ${index + 1}`"
        @click="remove(index)"
      >
        Remove
      </button>
    </div>
    <p v-if="problem" class="ui-error-box" role="alert">{{ problem }}</p>
    <div>
      <button
        type="button"
        class="secondary-button"
        :disabled="rows.length >= 32"
        @click="add"
      >
        Add provider ID
      </button>
    </div>
  </div>
</template>

<style scoped src="../styles/shared/editorForm.css"></style>
<style scoped>
.provider-ids {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.provider-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 2fr) auto;
  gap: 10px;
  align-items: end;
}
.secondary-button {
  min-height: var(--ui-control-height);
  padding: 0 14px;
  border: 1px solid var(--ui-border);
  border-radius: var(--ui-radius-control);
  background: transparent;
  color: var(--ui-text);
  font: inherit;
  font-size: 0.85rem;
  cursor: pointer;
}
.secondary-button:hover:not(:disabled) {
  border-color: var(--ui-accent-line);
}
.secondary-button:disabled {
  opacity: 0.55;
  cursor: default;
}
@media (max-width: 720px) {
  .provider-row {
    grid-template-columns: minmax(0, 1fr) auto;
  }
  .provider-row .field:first-child {
    grid-column: 1 / -1;
  }
}
</style>
