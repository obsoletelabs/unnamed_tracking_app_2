<script setup lang="ts">
// A compact "where does this value come from" label beside a metadata field.
// It appears when sources disagree, when the field is locked, or when a
// preferred source is not the one shown; selecting it lists what each source
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
  preferenceNotInEffect,
  showsFieldSource,
  sourceLabelFor,
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
// the server saved a preference but kept the value shown (applied: false)
const notApplied = ref(false);

const visible = computed(() => showsFieldSource(props.entry));
const fieldName = computed(() => FIELD_LABELS[props.field] ?? props.field);
const ignored = computed(() => preferenceNotInEffect(props.entry));
const preferredLabel = computed(() =>
  props.entry?.preferred
    ? sourceLabelFor(props.entry, props.entry.preferred)
    : "",
);
const chipTitle = computed(() => {
  if (props.entry?.locked) return `${fieldName.value}: locked`;
  if (ignored.value)
    return `${fieldName.value}: ${preferredLabel.value} is preferred but not shown`;
  return `${fieldName.value}: sources disagree`;
});

async function run(
  key: string,
  action: () => Promise<{ applied: boolean } | void>,
): Promise<void> {
  busy.value = key;
  error.value = null;
  notApplied.value = false;
  try {
    const result = await action();
    notApplied.value = !!result && !result.applied;
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
  <span
    v-if="visible && entry"
    class="field-source"
    @keydown.esc.stop="open = false"
  >
    <button
      :id="`field-source-${field}`"
      type="button"
      class="source-chip"
      :class="{ locked: entry.locked, warn: ignored && !entry.locked }"
      :aria-expanded="open"
      :title="chipTitle"
      @click="open = !open"
    >
      <svg
        v-if="entry.locked"
        class="chip-icon"
        viewBox="0 0 24 24"
        width="11"
        height="11"
        fill="none"
        stroke="currentColor"
        stroke-width="2.4"
        stroke-linecap="round"
        stroke-linejoin="round"
        aria-hidden="true"
      >
        <rect x="5" y="11" width="14" height="10" rx="2" />
        <path d="M8 11V7a4 4 0 0 1 8 0v4" />
      </svg>
      <span v-if="entry.locked" class="visually-hidden">Locked, </span>
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
      <p v-else-if="ignored && entry.origin === 'user'" class="note">
        You prefer {{ preferredLabel }} here, but the value shown was edited by
        hand.
      </p>
      <p v-else-if="ignored" class="note">
        You prefer {{ preferredLabel }} here, but the value shown comes from
        {{ currentSourceLabel(entry) }}. Only the value that won the last
        refresh is stored, so {{ preferredLabel }}'s value is not available to
        apply.
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
      <p v-if="notApplied" class="note" role="status">
        Preference saved. The value shown did not change.
      </p>
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
  align-items: center;
  display: inline-flex;
  gap: 4px;
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
.source-chip.locked {
  border-color: var(--ui-border-strong);
  color: var(--ui-text);
}
.source-chip.warn {
  background: var(--ui-warning-soft);
  border-color: var(--ui-warning);
  color: var(--ui-warning);
}
.chip-icon {
  flex: none;
}
.visually-hidden {
  clip: rect(0 0 0 0);
  height: 1px;
  overflow: hidden;
  position: absolute;
  white-space: nowrap;
  width: 1px;
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
  /* inside a section title or the hero credits, keep body text */
  color: var(--ui-text);
  font-weight: 400;
  letter-spacing: normal;
  text-align: left;
  text-transform: none;
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
