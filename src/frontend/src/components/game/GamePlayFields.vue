<script setup lang="ts">
import { computed } from "vue";
import type { GamePlayCompatibility } from "../../types/game";

const props = defineProps<{ modelValue: GamePlayCompatibility | null }>();
const emit = defineEmits<{
  "update:modelValue": [value: GamePlayCompatibility | null];
}>();
const empty = {
  minPlayers: null,
  maxPlayers: null,
  durationMinutes: null,
  crossplay: false,
};
const current = computed(() => props.modelValue ?? empty);
const hasLimits = computed(
  () => current.value.minPlayers !== null || current.value.maxPlayers !== null,
);
function update(value: Partial<GamePlayCompatibility>) {
  emit("update:modelValue", { ...current.value, ...value });
}
function number(event: Event) {
  const value = (event.target as HTMLInputElement).value;
  return value === "" ? null : Number(value);
}
</script>

<template>
  <fieldset class="play-fields">
    <legend>Multiplayer settings</legend>
    <label class="play-check">
      <input
        type="checkbox"
        :checked="modelValue !== null"
        @change="
          emit(
            'update:modelValue',
            ($event.target as HTMLInputElement).checked ? { ...empty } : null,
          )
        "
      />
      Playable with others
    </label>
    <p class="play-hint">
      You can save this without extra details. Unknown limits stay unknown.
    </p>
    <template v-if="modelValue !== null">
      <label class="play-check"
        ><input
          type="checkbox"
          :checked="hasLimits"
          @change="
            update(
              ($event.target as HTMLInputElement).checked
                ? { minPlayers: 1, maxPlayers: 4 }
                : { minPlayers: null, maxPlayers: null },
            )
          "
        />
        Set known player limits</label
      >
      <template v-if="hasLimits">
        <div class="play-presets" aria-label="Common player limits">
          <button
            v-for="range in [
              [1, 2],
              [1, 4],
              [2, 8],
            ]"
            :key="range.join('-')"
            type="button"
            class="ui-btn ui-btn-secondary ui-btn-sm"
            @click="update({ minPlayers: range[0]!, maxPlayers: range[1]! })"
          >
            {{ range[0] }}–{{ range[1] }} players
          </button>
        </div>
        <div class="play-grid">
          <label
            >Minimum players<input
              :value="current.minPlayers ?? ''"
              type="number"
              min="1"
              max="32"
              required
              @input="update({ minPlayers: number($event) })"
          /></label>
          <label
            >Maximum players<input
              :value="current.maxPlayers ?? ''"
              type="number"
              :min="current.minPlayers ?? 1"
              max="32"
              required
              @input="update({ maxPlayers: number($event) })"
          /></label>
        </div>
      </template>
      <details>
        <summary>Optional session details</summary>
        <label class="play-duration"
          >Typical session (minutes)<input
            :value="current.durationMinutes ?? ''"
            type="number"
            min="1"
            max="1440"
            placeholder="Unknown"
            @input="update({ durationMinutes: number($event) })"
        /></label>
        <label class="play-check"
          ><input
            type="checkbox"
            :checked="current.crossplay"
            @change="
              update({ crossplay: ($event.target as HTMLInputElement).checked })
            "
          />
          Cross-platform play confirmed</label
        >
      </details>
      <p class="play-hint">
        Self-reported. Saved platforms and player limits take precedence over
        Activity inference.
      </p>
    </template>
  </fieldset>
</template>

<style scoped>
.play-fields {
  border: 1px solid var(--ui-border);
  border-radius: var(--ui-radius-card);
  padding: 16px;
  margin: 12px 0;
  color: var(--ui-text);
}
legend {
  font-weight: 600;
  padding: 0 6px;
}
.play-check {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 10px 0;
}
.play-check input {
  width: 18px;
  height: 18px;
  accent-color: var(--ui-accent);
}
.play-hint {
  font-size: var(--ui-font-small);
  line-height: 1.5;
  color: var(--ui-dim);
  margin: 8px 0;
}
.play-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}
.play-grid label,
.play-duration {
  display: grid;
  gap: 6px;
  font-size: var(--ui-font-small);
}
input[type="number"] {
  width: 100%;
  box-sizing: border-box;
  min-height: 40px;
  padding: 8px 10px;
  color: var(--ui-text);
  background: var(--ui-bg);
  border: 1px solid var(--ui-border);
  border-radius: var(--ui-radius-control);
  font: inherit;
}
.play-presets {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin: 12px 0;
}
summary {
  cursor: pointer;
  font-size: var(--ui-font-small);
  margin-top: 14px;
}
.play-duration {
  max-width: 250px;
  margin: 12px 0;
}
input:focus-visible,
summary:focus-visible {
  outline: 2px solid var(--ui-accent);
  outline-offset: 3px;
}
@media (max-width: 380px) {
  .play-grid {
    grid-template-columns: 1fr;
  }
}
</style>
