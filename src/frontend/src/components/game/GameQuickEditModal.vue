<script setup lang="ts">
// The light game edit: status, platform, your rating and the cover. Saving
// sends only these fields, so everything else stored for the game is left
// alone. "Edit more" opens the full editor with the values and the artwork
// choice made here, unsaved, instead of discarding them.
import { computed, ref } from "vue";
import EditorShell from "../EditorShell.vue";
import GameArtPicker from "./GameArtPicker.vue";
import { quickUpdateGame } from "../../services/gameQuickEdit";
import {
  attachArtwork,
  stagedArtworkList,
} from "../../composables/useGameArtworkTransfer";
import { useStagedArtwork } from "../../composables/useStagedArtwork";
import { numberOrNull } from "../../utils/numberInput";
import { PLATFORM_SUGGESTIONS } from "../../utils/platforms";
import type { GameEditDraft } from "../../utils/gameEditDraft";
import type { Game, GameStatus } from "../../types/game";

const props = defineProps<{ game: Game; draft?: GameEditDraft | null }>();
const emit = defineEmits<{
  close: [];
  saved: [game: Game];
  more: [draft: GameEditDraft];
}>();

const STATUSES: { value: GameStatus; label: string }[] = [
  { value: "playing", label: "Playing" },
  { value: "beaten", label: "Beaten" },
  { value: "mastered", label: "Mastered" },
  { value: "played", label: "Played" },
  { value: "on hold", label: "On hold" },
  { value: "dropped", label: "Dropped" },
  { value: "backlog", label: "Backlog" },
  { value: "wishlist", label: "Wishlist" },
];

const status = ref<GameStatus>(props.draft?.status ?? props.game.status);
const platform = ref(props.draft?.platform ?? props.game.platform ?? "");
const rating = ref<number | string | null>(
  props.draft ? props.draft.ratingOverall : props.game.ratingOverall,
);
const cover = useStagedArtwork(
  () => props.game.coverImageUrl,
  props.draft?.cover ?? null,
);
const saving = ref(false);
const error = ref<string | null>(null);
const dirty = computed(
  () =>
    status.value !== props.game.status ||
    platform.value.trim() !== (props.game.platform ?? "") ||
    (numberOrNullSafe(rating.value) ?? null) !==
      (props.game.ratingOverall ?? null) ||
    !!cover.staged.value,
);
function numberOrNullSafe(value: unknown): number | null | undefined {
  try {
    return numberOrNull(value);
  } catch {
    return undefined;
  }
}

async function save() {
  if (saving.value) return;
  error.value = null;
  let ratingOverall: number | null;
  try {
    ratingOverall = numberOrNull(rating.value, "Your rating", {
      min: 0,
      max: 10,
    });
  } catch (failure) {
    error.value =
      failure instanceof Error ? failure.message : "Check your rating.";
    return;
  }
  saving.value = true;
  // Captured before awaiting: a retry only ever targets this game.
  const pending = stagedArtworkList([
    { kind: "key_art", label: "cover", choice: cover.staged.value },
  ]);
  try {
    const saved = await quickUpdateGame(props.game.id, {
      status: status.value,
      platform: platform.value.trim() || null,
      ratingOverall,
    });
    if (import.meta.env.VITE_USE_MOCK_DATA !== "true" && pending.length)
      await attachArtwork(saved.id, saved.title, pending);
    emit("saved", saved);
  } catch (failure) {
    error.value =
      failure instanceof Error ? failure.message : "Could not save the game.";
  } finally {
    saving.value = false;
  }
}

function editMore() {
  emit("more", {
    status: status.value,
    platform: platform.value,
    ratingOverall: rating.value,
    cover: cover.toChoice(),
  });
}
</script>

<template>
  <EditorShell
    title="Quick edit"
    :heading="game.title"
    subtitle="Status, platform, rating and cover"
    :cover="cover.preview.value"
    size="standard"
    :dismissible="!saving"
    class="game-quick-edit"
    @close="emit('close')"
    @submit="save"
  >
    <div class="editor-grid">
      <label class="field">
        <span>Status</span>
        <select v-model="status">
          <option v-for="s in STATUSES" :key="s.value" :value="s.value">
            {{ s.label }}
          </option>
        </select>
      </label>
      <label class="field">
        <span>Platform</span>
        <input
          v-model="platform"
          type="text"
          list="quick-edit-platforms"
          autocomplete="off"
          placeholder="PC"
        />
        <datalist id="quick-edit-platforms">
          <option v-for="p in PLATFORM_SUGGESTIONS" :key="p" :value="p" />
        </datalist>
      </label>
      <label class="field">
        <span>Your rating (0–10)</span>
        <input
          v-model.number="rating"
          type="number"
          min="0"
          max="10"
          step="0.1"
          placeholder="–"
        />
      </label>
    </div>
    <GameArtPicker
      label="Cover"
      shape="tall"
      :current="game.coverImageUrl"
      :staged="cover.staged.value"
      :candidates="[]"
      :disabled="saving"
      @file="cover.chooseFile"
      @url="cover.chooseUrl"
      @reset="cover.reset"
    />
    <p class="hint">
      Offered search covers and every other field are in the full editor.
    </p>

    <template v-if="error" #status>
      <div class="form-error" role="alert">{{ error }}</div>
    </template>

    <template #actions="{ formId }">
      <button
        type="button"
        class="secondary-button"
        :disabled="saving"
        @click="editMore"
      >
        Edit more
      </button>
      <span class="modal-actions-spacer"></span>
      <button
        type="button"
        class="secondary-button"
        :disabled="saving"
        @click="emit('close')"
      >
        Cancel
      </button>
      <button
        type="submit"
        :form="formId"
        class="primary-button"
        :disabled="saving || !dirty"
      >
        {{ saving ? "Saving…" : "Save" }}
      </button>
    </template>
  </EditorShell>
</template>

<style scoped src="../../styles/shared/editorForm.css"></style>
<style scoped src="../../styles/shared/editorActions.css"></style>
<style scoped>
.form-error {
  color: var(--ui-error);
  font-size: 0.85rem;
  background: color-mix(in srgb, var(--ui-error) 10%, transparent);
  border: 1px solid color-mix(in srgb, var(--ui-error) 30%, transparent);
  border-radius: var(--ui-radius-control);
  padding: 10px 12px;
}
</style>
