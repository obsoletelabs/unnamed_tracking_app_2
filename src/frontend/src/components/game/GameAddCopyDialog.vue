<script setup lang="ts">
// Adds another library entry to this game as a copy (another store, platform or
// edition). Each copy keeps its own saves, playtime and imports; the game's page
// shows them together (#274). Suggestions come from the review queue, so the
// dialog and Settings → Library → Duplicates always agree.
import { computed, onMounted, ref, useId } from "vue";
import UiModal from "../UiModal.vue";
import { fetchGames } from "../../services/games";
import { mergeGames } from "../../services/gameIdentity";
import {
  fetchReviewQueue,
  mergeReviewEntry,
} from "../../services/identityReview";
import type { Game } from "../../types/game";
import { gameCopyName, gamePlaytimeSeconds } from "../../utils/gameCopies";
import { hoursLabel } from "../../utils/identityDisplay";

const props = defineProps<{
  gameId: string;
  title: string;
  /** Games already in this game's group, which cannot be added again. */
  members: string[];
}>();
const emit = defineEmits<{ close: []; added: [] }>();

const id = useId();
const games = ref<Game[]>([]);
const suggestions = ref<Map<string, string>>(new Map()); // game id -> queue entry id
const query = ref("");
const chosen = ref<string | null>(null);
const makeMain = ref(false);
const loading = ref(true);
const busy = ref(false);
const error = ref<string | null>(null);

onMounted(async () => {
  try {
    const [library, queue] = await Promise.all([
      fetchGames(),
      fetchReviewQueue().catch(() => ({ entries: [], has_more: false })),
    ]);
    games.value = library;
    const found = new Map<string, string>();
    for (const entry of queue.entries) {
      if (entry.first.id === props.gameId) found.set(entry.second.id, entry.id);
      else if (entry.second.id === props.gameId)
        found.set(entry.first.id, entry.id);
    }
    suggestions.value = found;
  } catch (cause) {
    error.value =
      cause instanceof Error ? cause.message : "Could not load your games.";
  } finally {
    loading.value = false;
  }
});

const candidates = computed(() => {
  const excluded = new Set([props.gameId, ...props.members]);
  const q = query.value.trim().toLowerCase();
  return games.value
    .filter((g) => !excluded.has(g.id))
    .filter(
      (g) =>
        !q ||
        g.title.toLowerCase().includes(q) ||
        gameCopyName(g).toLowerCase().includes(q),
    )
    .sort(
      (a, b) =>
        Number(suggestions.value.has(b.id)) -
          Number(suggestions.value.has(a.id)) || a.title.localeCompare(b.title),
    )
    .slice(0, 60);
});
const chosenGame = computed(
  () => games.value.find((g) => g.id === chosen.value) ?? null,
);

async function add(): Promise<void> {
  const game = chosenGame.value;
  if (!game) return;
  busy.value = true;
  error.value = null;
  try {
    const entry = suggestions.value.get(game.id);
    const main = makeMain.value ? game.id : props.gameId;
    if (entry) await mergeReviewEntry(entry, main);
    else
      await mergeGames(
        game.id,
        props.gameId,
        true,
        makeMain.value ? "source" : "target",
      );
    emit("added");
  } catch (cause) {
    error.value =
      cause instanceof Error ? cause.message : "The copy could not be added.";
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <UiModal
    :title="`Add a copy of ${title}`"
    description="Pick the same game from another store, platform or edition. Each copy keeps its own saves, playtime and imports; this page shows them together."
    size="wide"
    @close="emit('close')"
  >
    <label :for="`${id}-q`" class="label">Find a game in your library</label>
    <input
      :id="`${id}-q`"
      v-model="query"
      class="ui-field"
      type="search"
      placeholder="Title, store or platform"
      autocomplete="off"
    />
    <p v-if="loading" class="hint" role="status">Loading your library…</p>
    <p v-else-if="!candidates.length" class="hint">
      {{
        query
          ? "Nothing matches."
          : "Add or import the other copy first, then group it here."
      }}
    </p>
    <ul
      v-else
      class="choices"
      role="radiogroup"
      :aria-label="`Copies to add to ${title}`"
    >
      <li v-for="g in candidates" :key="g.id">
        <label class="choice" :class="{ picked: chosen === g.id }">
          <input
            v-model="chosen"
            type="radio"
            :name="`${id}-copy`"
            :value="g.id"
          />
          <span class="choice-main">
            <span class="choice-title">{{ g.title }}</span>
            <span class="choice-copy">{{ gameCopyName(g) }}</span>
          </span>
          <span class="choice-facts">
            <span v-if="suggestions.has(g.id)" class="suggested"
              >Suggested</span
            >
            <span>{{ hoursLabel(gamePlaytimeSeconds(g)) }}</span>
          </span>
        </label>
      </li>
    </ul>
    <label v-if="chosenGame" class="main-toggle">
      <input v-model="makeMain" type="checkbox" />
      Make {{ gameCopyName(chosenGame) }} the main copy (its title, cover and
      details lead)
    </label>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <template #footer>
      <button type="button" class="ui-btn ui-btn-ghost" @click="emit('close')">
        Cancel
      </button>
      <button
        type="button"
        class="ui-btn ui-btn-primary"
        :disabled="!chosenGame || busy"
        @click="add"
      >
        {{ busy ? "Adding…" : "Add as a copy" }}
      </button>
    </template>
  </UiModal>
</template>

<style scoped>
.label {
  display: block;
  margin-bottom: 6px;
  color: var(--ui-dim);
  font-size: 0.85rem;
}
.ui-field {
  width: 100%;
}
.hint {
  color: var(--ui-dim);
}
.error {
  color: var(--ui-danger);
}
.choices {
  list-style: none;
  margin: 12px 0 0;
  padding: 0;
  max-height: min(50vh, 420px);
  overflow-y: auto;
  display: grid;
  gap: 6px;
}
.choice {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 12px;
  border: 1px solid var(--ui-border);
  border-radius: var(--ui-radius-control);
  cursor: pointer;
  min-width: 0;
}
.choice:hover,
.choice.picked {
  border-color: var(--ui-accent-line);
  background: var(--ui-accent-soft);
}
.choice input {
  flex: none;
}
.choice-main {
  display: grid;
  min-width: 0;
  flex: 1;
}
.choice-title {
  font-weight: 600;
  overflow-wrap: anywhere;
}
.choice-copy {
  color: var(--ui-dim);
  font-size: 0.85rem;
}
.choice-facts {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--ui-dim);
  font-size: 0.85rem;
  font-variant-numeric: tabular-nums;
  flex: none;
}
.suggested {
  padding: 1px 8px;
  border-radius: 999px;
  border: 1px solid var(--ui-accent-line);
  color: var(--ui-text);
  font-size: 0.72rem;
  font-weight: 600;
}
.main-toggle {
  display: flex;
  gap: 8px;
  align-items: flex-start;
  margin-top: 14px;
}
@media (max-width: 560px) {
  .choice {
    flex-wrap: wrap;
  }
  .choice-facts {
    width: 100%;
    padding-left: 26px;
  }
}
</style>
