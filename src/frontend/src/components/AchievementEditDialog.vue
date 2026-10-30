<script setup lang="ts">
import { computed, ref, useId } from "vue";
import UiModal from "./UiModal.vue";
import {
  isUnlocked,
  localInputToUnixSeconds,
  toLocalInput,
} from "../utils/achievements";
import type { Achievement } from "../types/game";

type AchievementProgress = "synced" | "unlocked" | "locked";

const props = defineProps<{
  achievement: Achievement;
  note: string;
  saving: boolean;
  error: string;
}>();

const emit = defineEmits<{
  close: [];
  save: [progress: AchievementProgress, note: string, earnedAt: number | null];
}>();

const id = useId();
const progress = ref<AchievementProgress>(
  props.achievement.manualOverride
    ? isUnlocked(props.achievement)
      ? "unlocked"
      : "locked"
    : "synced",
);
const note = ref(props.note);
const now = () => toLocalInput(new Date());
// The date it was earned; a new unlock starts at the current time.
const earnedAt = ref(
  props.achievement.unlockedAt
    ? toLocalInput(new Date(props.achievement.unlockedAt))
    : now(),
);
const earnedAtInvalid = computed(() => {
  const seconds = localInputToUnixSeconds(earnedAt.value);
  return (
    progress.value === "unlocked" &&
    (seconds === null || seconds > Date.now() / 1000 + 60)
  );
});
const options: {
  value: AchievementProgress;
  label: string;
  description: string;
}[] = [
  {
    value: "synced",
    label: "Synced",
    description: "Follow the latest provider progress",
  },
  {
    value: "unlocked",
    label: "Unlocked",
    description: "Keep this marked as earned",
  },
  {
    value: "locked",
    label: "Locked",
    description: "Keep this marked as not earned",
  },
];

function save() {
  if (props.saving || earnedAtInvalid.value) return;
  emit(
    "save",
    progress.value,
    note.value,
    progress.value === "unlocked"
      ? localInputToUnixSeconds(earnedAt.value)
      : null,
  );
}
</script>

<template>
  <UiModal
    title="Edit achievement"
    :description="achievement.name"
    size="standard"
    @close="emit('close')"
  >
    <div class="achievement-edit-content">
      <fieldset class="achievement-edit-progress">
        <legend>Progress</legend>
        <label
          v-for="option in options"
          :key="option.value"
          class="achievement-edit-option"
          :class="{ selected: progress === option.value }"
          :for="`${id}-${option.value}`"
        >
          <input
            :id="`${id}-${option.value}`"
            v-model="progress"
            type="radio"
            name="achievement-progress"
            :value="option.value"
          />
          <span class="achievement-edit-option-copy">
            <strong>{{ option.label }}</strong>
            <small>{{ option.description }}</small>
          </span>
        </label>
      </fieldset>

      <template v-if="progress === 'unlocked'">
        <label class="achievement-edit-note-label" :for="`${id}-earned`">
          Earned on
        </label>
        <input
          :id="`${id}-earned`"
          v-model="earnedAt"
          type="datetime-local"
          class="ui-field achievement-edit-earned"
          :max="now()"
          required
        />
        <p v-if="earnedAtInvalid" class="achievement-edit-error" role="alert">
          Choose a date and time that is not in the future.
        </p>
      </template>

      <label class="achievement-edit-note-label" :for="`${id}-note`">
        Note
      </label>
      <textarea
        :id="`${id}-note`"
        v-model="note"
        class="ui-field achievement-edit-note"
        rows="4"
        placeholder="How you got it, or how you plan to"
      ></textarea>
      <p class="achievement-edit-hint">
        Saved with your local achievement notes.
      </p>
      <p v-if="error" class="achievement-edit-error" role="alert">
        {{ error }}
      </p>
    </div>

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
        :disabled="saving || earnedAtInvalid"
        @click="save"
      >
        {{ saving ? "Saving…" : "Save changes" }}
      </button>
    </template>
  </UiModal>
</template>

<style scoped>
.achievement-edit-content {
  display: grid;
  gap: 10px;
}

.achievement-edit-progress {
  display: grid;
  gap: 8px;
  min-width: 0;
  margin: 0 0 6px;
  padding: 0;
  border: 0;
}

.achievement-edit-progress legend,
.achievement-edit-note-label {
  margin-bottom: 8px;
  color: var(--ui-text);
  font-size: 0.9rem;
  font-weight: 700;
}

.achievement-edit-option {
  display: flex;
  align-items: flex-start;
  gap: 11px;
  padding: 11px 12px;
  border: 1px solid var(--ui-border);
  border-radius: var(--ui-radius-control);
  background: var(--ui-surface);
  cursor: pointer;
}

.achievement-edit-option.selected {
  border-color: var(--ui-accent);
  background: color-mix(in srgb, var(--ui-accent) 8%, var(--ui-surface));
}

.achievement-edit-option input {
  margin: 4px 0 0;
  accent-color: var(--ui-accent);
}

.achievement-edit-option-copy {
  display: grid;
  gap: 2px;
}

.achievement-edit-option-copy strong {
  color: var(--ui-text);
  font-size: 0.9rem;
}

.achievement-edit-option-copy small,
.achievement-edit-hint {
  color: var(--ui-dim);
  font-size: 0.78rem;
}

.achievement-edit-note-label {
  margin: 0;
}

.achievement-edit-note {
  box-sizing: border-box;
  width: 100%;
  min-height: 96px;
  resize: vertical;
}

.achievement-edit-earned {
  box-sizing: border-box;
  width: 100%;
}

.achievement-edit-hint {
  margin: -5px 0 0;
}

.achievement-edit-error {
  margin: 0;
  color: var(--ui-danger, #c33);
  font-size: 0.85rem;
}

@media (min-width: 560px) {
  .achievement-edit-progress {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }

  .achievement-edit-progress legend {
    grid-column: 1 / -1;
  }
}
</style>
