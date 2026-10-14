<script setup lang="ts">
import { computed, ref, watch } from "vue";
import type { Game, GamePlayCompatibility } from "../../types/game";
import { setGamePlayCompatibility } from "../../services/games";
import GamePlayFields from "./GamePlayFields.vue";
const props = defineProps<{ game: Game }>();
const emit = defineEmits<{ saved: [] }>();
const editing = ref(false),
  saving = ref(false),
  error = ref("");
const draft = ref<GamePlayCompatibility | null>(null);
const detected = computed(() =>
  props.game.features.some((value) =>
    /multiplayer|multi-player|co-op|coop/i.test(value),
  ),
);
const summary = computed(() => {
  const preset = props.game.playCompatibility;
  return preset
    ? preset.minPlayers !== null && preset.maxPlayers !== null
      ? `${preset.minPlayers}–${preset.maxPlayers} players`
      : "Multiplayer · Player limits unknown"
    : detected.value
      ? "Multiplayer detected · Add details if needed"
      : "Add multiplayer details when you know them";
});
function edit() {
  draft.value = props.game.playCompatibility
    ? { ...props.game.playCompatibility }
    : null;
  error.value = "";
  editing.value = true;
}
watch(
  () => props.game.id,
  () => {
    editing.value = false;
    error.value = "";
  },
);
async function save() {
  saving.value = true;
  error.value = "";
  try {
    await setGamePlayCompatibility(props.game.id, draft.value);
    editing.value = false;
    emit("saved");
  } catch (err) {
    error.value =
      err instanceof Error
        ? err.message
        : "Unable to save multiplayer settings.";
  } finally {
    saving.value = false;
  }
}
</script>
<template>
  <section class="play-panel" aria-label="Multiplayer settings">
    <div class="play-heading">
      <div>
        <h3>Multiplayer</h3>
        <p>{{ summary }}</p>
      </div>
      <button
        v-if="!editing"
        type="button"
        class="ui-btn ui-btn-secondary ui-btn-sm"
        @click="edit"
      >
        Edit multiplayer settings
      </button>
    </div>
    <form v-if="editing" @submit.prevent="save">
      <GamePlayFields v-model="draft" />
      <p v-if="error" role="alert">{{ error }}</p>
      <div class="play-actions">
        <button class="ui-btn ui-btn-primary" :disabled="saving">
          {{ saving ? "Saving…" : "Save multiplayer settings" }}</button
        ><button
          type="button"
          class="ui-btn ui-btn-secondary"
          :disabled="saving"
          @click="editing = false"
        >
          Cancel
        </button>
      </div>
    </form>
    <p class="play-footnote">
      Activities use these details after refreshing your library. Select several
      games in the library to edit them together.
    </p>
  </section>
</template>
<style scoped>
.play-panel {
  padding: 16px;
  border: 1px solid var(--ui-border);
  border-radius: var(--ui-radius-card);
  background: var(--ui-surface);
  margin: 20px 0;
}
.play-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}
h3 {
  font-size: var(--ui-font-heading);
  margin: 0 0 4px;
}
p {
  color: var(--ui-dim);
  font-size: var(--ui-font-small);
  margin: 0;
  line-height: 1.5;
}
.play-footnote {
  margin-top: 12px;
}
.play-actions {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
}
</style>
