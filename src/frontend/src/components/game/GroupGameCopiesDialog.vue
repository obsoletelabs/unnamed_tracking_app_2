<script setup lang="ts">
import { computed, ref } from "vue";
import UiModal from "../UiModal.vue";
import { setOwnedCopy } from "../../services/gameOwnership";

interface Choice {
  id: string;
  title: string;
  source: string | null;
  platform: string | null;
}
const props = defineProps<{ games: Choice[]; mainId?: string }>();
const emit = defineEmits<{ close: []; grouped: [mainId: string] }>();
const mainId = ref(props.mainId ?? props.games[0]?.id ?? "");
const copyId = ref(
  props.games.find((game) => game.id !== mainId.value)?.id ?? "",
);
const candidates = computed(() =>
  props.games.filter((game) => game.id !== mainId.value),
);
const saving = ref(false);
const error = ref<string | null>(null);
function label(game: Choice): string {
  return `${game.title} · ${game.source || "Manual entry"} · ${game.platform || "Unspecified platform"}`;
}
function chooseMain(): void {
  if (mainId.value === copyId.value)
    copyId.value = candidates.value[0]?.id ?? "";
}
async function save(): Promise<void> {
  saving.value = true;
  error.value = null;
  try {
    await setOwnedCopy(mainId.value, copyId.value, true);
    emit("grouped", mainId.value);
  } catch (cause) {
    error.value =
      cause instanceof Error ? cause.message : "Could not group these copies.";
  } finally {
    saving.value = false;
  }
}
</script>

<template>
  <UiModal
    title="Group owned copies"
    :dismissible="!saving"
    @close="emit('close')"
  >
    <p>
      Show one main library entry. Use its status and notes for overall
      tracking; each copy keeps its own progress, notes, achievements, imports
      and files. You can separate copies later from the main game's Owned copies
      section.
    </p>
    <form id="group-copies-form" class="choices" @submit.prevent="save">
      <label>
        Main game
        <select
          v-model="mainId"
          :disabled="saving || !!props.mainId"
          @change="chooseMain"
        >
          <option v-for="game in games" :key="game.id" :value="game.id">
            {{ label(game) }}
          </option>
        </select>
      </label>
      <label>
        Owned copy
        <select v-model="copyId" :disabled="saving">
          <option v-for="game in candidates" :key="game.id" :value="game.id">
            {{ label(game) }}
          </option>
        </select>
      </label>
      <p v-if="error" class="group-error" role="alert">{{ error }}</p>
    </form>
    <template #footer>
      <button
        class="ui-btn ui-btn-secondary"
        type="button"
        :disabled="saving"
        @click="emit('close')"
      >
        Cancel
      </button>
      <button
        class="ui-btn ui-btn-primary"
        form="group-copies-form"
        type="submit"
        :disabled="saving || !mainId || !copyId || mainId === copyId"
      >
        {{ saving ? "Grouping…" : "Group copies" }}
      </button>
    </template>
  </UiModal>
</template>

<style scoped>
.choices {
  display: grid;
  gap: 16px;
}
label {
  display: grid;
  gap: 6px;
  min-width: 0;
}
select {
  width: 100%;
  min-width: 0;
}
p {
  line-height: 1.5;
  overflow-wrap: anywhere;
}
.group-error {
  color: var(--ui-danger);
}
</style>
