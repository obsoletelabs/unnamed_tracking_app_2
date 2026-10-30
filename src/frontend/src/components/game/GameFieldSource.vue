<script setup lang="ts">
// A compact "where does this value come from" label beside a metadata field.
// It appears only when sources disagree; selecting it lists what each source
// reports, with the current value marked and a "Prefer this source" action
// (#348, #349). A lock still wins: the list says so instead of hiding it.
import { computed, ref } from "vue";
import {
  clearFieldSource,
  preferFieldSource,
  type FieldSourceEntry,
} from "../../services/gameFieldSources";
import {
  FIELD_LABELS,
  currentSourceLabel,
  formatSourceValue,
  sourcesDisagree,
} from "../../utils/fieldSources";

const props = defineProps<{
  gameId: string;
  field: string;
  entry?: FieldSourceEntry;
}>();
const emit = defineEmits<{ changed: [] }>();

const open = ref(false);
const busy = ref<string | null>(null);
const error = ref<string | null>(null);

const visible = computed(() => sourcesDisagree(props.entry));
const fieldName = computed(() => FIELD_LABELS[props.field] ?? props.field);

async function run(key: string, action: () => Promise<unknown>): Promise<void> {
  busy.value = key;
  error.value = null;
  try {
    await action();
    emit("changed");
  } catch (cause) {
    error.value =
      cause instanceof Error ? cause.message : "That change did not work.";
  } finally {
    busy.value = null;
  }
}

const prefer = (key: string) =>
  run(key, () => preferFieldSource(props.gameId, props.field, key));
const clear = () =>
  run("clear", () => clearFieldSource(props.gameId, props.field));
</script>

<template>
  <span v-if="visible && entry" class="field-source">
    <button
      :id="`field-source-${field}`"
      type="button"
      class="source-chip"
      :aria-expanded="open"
      :title="`${fieldName}: sources disagree`"
      @click="open = !open"
    >
      {{ currentSourceLabel(entry) }}
    </button>
    <div
      v-if="open"
      class="source-list"
      role="group"
      :aria-label="`${fieldName} sources`"
    >
      <p v-if="entry.locked" class="note">
        This field is locked, so a refresh keeps the current value.
      </p>
      <ul>
        <li v-for="source in entry.sources" :key="source.sourceKey">
          <div class="row-head">
            <strong>{{ source.label }}</strong>
            <span v-if="source.current" class="tag">Current</span>
            <span
              v-if="entry.preferred === source.sourceKey"
              class="tag preferred"
              >Preferred</span
            >
          </div>
          <p class="value">{{ formatSourceValue(source.value) }}</p>
          <button
            v-if="entry.preferred !== source.sourceKey"
            type="button"
            class="ui-btn ui-btn-secondary"
            :disabled="busy !== null"
            @click="prefer(source.sourceKey)"
          >
            {{ busy === source.sourceKey ? "Saving…" : "Prefer this source" }}
          </button>
        </li>
      </ul>
      <button
        v-if="entry.preferred"
        type="button"
        class="ui-btn ui-btn-secondary"
        :disabled="busy !== null"
        @click="clear"
      >
        Clear preference
      </button>
      <p v-if="error" class="error" role="alert">{{ error }}</p>
    </div>
  </span>
</template>

<style scoped>
.field-source {
  display: inline-block;
  margin-left: 6px;
  vertical-align: middle;
}
.source-chip {
  background: transparent;
  border: 1px solid var(--ui-border);
  border-radius: 999px;
  color: var(--ui-dim);
  cursor: pointer;
  font: inherit;
  font-size: 0.72rem;
  font-weight: 600;
  padding: 1px 8px;
}
.source-list {
  background: var(--ui-surface, transparent);
  border: 1px solid var(--ui-border);
  border-radius: 10px;
  display: grid;
  font-size: 0.85rem;
  gap: 8px;
  margin-top: 6px;
  max-width: 28rem;
  padding: 10px 12px;
}
ul {
  display: grid;
  gap: 8px;
  list-style: none;
  margin: 0;
  padding: 0;
}
.row-head {
  align-items: center;
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.value {
  color: var(--ui-dim);
  margin: 2px 0 4px;
  overflow-wrap: anywhere;
}
.note {
  color: var(--ui-dim);
  margin: 0;
}
.tag {
  border: 1px solid var(--ui-border);
  border-radius: 999px;
  color: var(--ui-dim);
  font-size: 0.72rem;
  font-weight: 600;
  padding: 1px 8px;
}
.tag.preferred {
  background: var(--ui-accent-soft);
  border-color: var(--ui-accent-line);
  color: var(--ui-text);
}
.error {
  color: var(--ui-danger);
  margin: 0;
}
</style>
