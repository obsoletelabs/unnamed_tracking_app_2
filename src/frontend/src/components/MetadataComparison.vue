<script setup lang="ts">
import { computed } from "vue";
import type { MetadataChange } from "../composables/useMetadataComparison";

const props = defineProps<{
  changes: MetadataChange[];
  provider: string;
  busy?: boolean;
  reason?: string | null;
  previews?: Record<string, string>;
}>();
const emit = defineEmits<{
  toggle: [key: string, checked: boolean];
  apply: [];
  cancel: [];
}>();
const checked = computed(
  () => props.changes.filter((change) => change.checked).length,
);
function imageUrl(value: unknown): value is string {
  return (
    typeof value === "string" && /^(https?:\/\/|\/(?!\/)|blob:)/.test(value)
  );
}
function describe(value: unknown): string {
  if (value == null || value === "") return "Empty";
  if (Array.isArray(value))
    return value.length ? value.map(describe).join(", ") : "Empty";
  if (typeof value === "object")
    return (
      Object.entries(value)
        .filter(
          ([, item]) =>
            item != null &&
            item !== "" &&
            (!Array.isArray(item) || item.length),
        )
        .map(
          ([key, item]) =>
            `${key.replace(/([A-Z])/g, " $1").replaceAll("_", " ")}: ${describe(item)}`,
        )
        .join(" · ") || "Empty"
    );
  return String(value);
}
</script>

<template>
  <section class="metadata-comparison" aria-label="Review metadata changes">
    <div class="comparison-heading">
      <div>
        <h3>Look up metadata</h3>
        <p>
          Choose what to apply from {{ provider }}. Changes stay in this form
          until Save.
        </p>
      </div>
      <button type="button" class="ui-btn ui-btn-ghost" @click="emit('cancel')">
        Cancel lookup
      </button>
    </div>
    <p v-if="busy" role="status">Gathering details and verifying artwork…</p>
    <p v-if="reason" role="status">{{ reason }}</p>
    <p v-else-if="!changes.length && !busy">
      No different fields are available from this result.
    </p>
    <div class="comparison-fields">
      <div
        v-for="change in changes"
        :key="change.key"
        class="comparison-change"
        :class="{ locked: change.locked, checked: change.checked }"
      >
        <label>
          <input
            type="checkbox"
            :checked="change.checked"
            :disabled="change.locked"
            @change="
              emit(
                'toggle',
                change.key,
                ($event.target as HTMLInputElement).checked,
              )
            "
          />
          <strong>{{ change.label }}</strong
          ><span v-if="change.locked">Protected</span>
        </label>
        <div class="comparison-values">
          <div>
            <span class="comparison-caption">Current</span>
            <p>{{ describe(change.current) }}</p>
            <img
              v-if="change.image && imageUrl(change.current)"
              :src="change.current"
              alt="Current artwork"
            />
          </div>
          <div>
            <span class="comparison-caption">Offered</span>
            <p>
              {{ change.image ? "Verified artwork" : describe(change.offered) }}
            </p>
            <img
              v-if="
                change.image &&
                typeof change.offered === 'string' &&
                previews?.[change.offered]
              "
              :src="previews[change.offered]"
              alt="Offered artwork"
            />
          </div>
        </div>
      </div>
    </div>
    <div class="comparison-actions">
      <p>
        Empty editable fields are selected. Protected fields stay unchanged.
      </p>
      <button
        type="button"
        class="ui-btn ui-btn-primary"
        :disabled="!checked"
        @click="emit('apply')"
      >
        Apply {{ checked }} {{ checked === 1 ? "change" : "changes" }}
      </button>
    </div>
  </section>
</template>

<style scoped>
.metadata-comparison {
  background: var(--ui-popover, var(--ui-surface));
  border: 1px solid var(--ui-border);
  border-radius: var(--ui-radius-dialog);
  padding: 16px;
  color: var(--ui-text);
  min-width: 0;
  display: flex;
  flex-direction: column;
  max-height: min(58dvh, 540px);
  flex-shrink: 0;
}
.comparison-fields {
  min-height: 0;
  overflow-y: auto;
  flex: 1;
}
.comparison-heading,
.comparison-actions {
  flex-shrink: 0;
}
.comparison-heading,
.comparison-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}
h3 {
  margin: 0;
  color: var(--ui-accent-text);
  font-size: 0.9rem;
}
p {
  margin: 4px 0;
  overflow-wrap: anywhere;
  white-space: pre-wrap;
}
.comparison-heading p,
.comparison-actions p,
.comparison-caption,
.locked label span {
  color: var(--ui-dim);
  font-size: var(--ui-font-small);
}
.comparison-change {
  margin: 10px 0;
  padding: 12px;
  border: 1px solid var(--ui-border);
  border-radius: var(--ui-radius-control);
  background: var(--ui-surface);
}
.comparison-change.checked {
  border-color: var(--ui-accent-text);
}
.comparison-change label {
  display: flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
}
.comparison-change input {
  accent-color: var(--ui-accent-text);
  width: 18px;
  height: 18px;
}
.comparison-values {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
  margin-top: 8px;
  font-size: var(--ui-font-small);
  max-height: 240px;
  overflow-y: auto;
}
.comparison-values img {
  max-width: 100%;
  max-height: 140px;
  object-fit: contain;
}
.comparison-actions {
  border-top: 1px solid var(--ui-border);
  padding-top: 12px;
}
@media (max-width: 480px) {
  .comparison-values {
    grid-template-columns: 1fr;
  }
}
</style>
