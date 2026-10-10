<script setup lang="ts">
import { computed, ref } from "vue";
import type { Game } from "../../types/game";
import { fetchGames } from "../../services/games";
import { setOwnedCopy } from "../../services/gameOwnership";
import { useConfirm } from "../../state/dialog";
import GroupGameCopiesDialog from "./GroupGameCopiesDialog.vue";

const props = defineProps<{ game: Game; copies: Game[] }>();
const emit = defineEmits<{ changed: [] }>();
const confirm = useConfirm();
const busy = ref(false);
const error = ref<string | null>(null);
const choices = ref<Game[] | null>(null);
const members = computed(() => [props.game, ...props.copies]);
const minutes = computed(() =>
  members.value.reduce(
    (sum, game) =>
      sum +
      game.platforms.reduce(
        (total, platform) => total + platform.playtimeMinutes,
        0,
      ),
    0,
  ),
);
const choiceSummaries = computed(
  () =>
    choices.value?.map((game) => ({
      id: game.id,
      title: game.title,
      source: game.source,
      platform: game.platforms[0]?.platform ?? null,
    })) ?? [],
);
const isCopy = computed(
  () =>
    props.game.relationshipType === "owned_copy" && !!props.game.parentGameId,
);

function time(game: Game): string {
  return `${game.platforms.reduce((sum, platform) => sum + platform.playtimeMinutes, 0).toLocaleString()} min`;
}
async function addCopy(): Promise<void> {
  busy.value = true;
  error.value = null;
  try {
    const games = await fetchGames();
    const candidates = games.filter(
      (game) => !game.parentGameId && game.id !== props.game.id,
    );
    if (!candidates.length)
      error.value =
        "Add or import another game entry first, then group it here.";
    else choices.value = [props.game, ...candidates];
  } catch (cause) {
    error.value =
      cause instanceof Error ? cause.message : "Could not load your games.";
  } finally {
    busy.value = false;
  }
}
async function separate(game: Game): Promise<void> {
  const mainId = game.parentGameId;
  if (
    !mainId ||
    !(await confirm({
      title: "Separate owned copy",
      message:
        "Show this copy as its own library entry? Its progress, notes, imports and files will stay intact.",
      confirmLabel: "Separate copy",
    }))
  )
    return;
  busy.value = true;
  error.value = null;
  try {
    await setOwnedCopy(mainId, game.id, false);
    emit("changed");
  } catch (cause) {
    error.value =
      cause instanceof Error ? cause.message : "Could not separate this copy.";
  } finally {
    busy.value = false;
  }
}
function grouped(): void {
  choices.value = null;
  emit("changed");
}
</script>

<template>
  <section
    v-if="isCopy || !game.parentGameId"
    class="owned-copies"
    aria-labelledby="owned-copies-title"
  >
    <header>
      <h2 id="owned-copies-title">
        {{ isCopy ? "Owned copy" : "Owned copies" }}
      </h2>
      <button
        v-if="!isCopy"
        class="ui-btn ui-btn-secondary"
        type="button"
        :disabled="busy"
        @click="addCopy"
      >
        Add owned copy
      </button>
    </header>
    <template v-if="isCopy">
      <p>
        Overall tracking is on the
        <RouterLink :to="`/games/${game.parentGameId}`">main game</RouterLink>.
        This copy keeps its own progress and files.
      </p>
      <button
        class="ui-btn ui-btn-secondary"
        type="button"
        :disabled="busy"
        @click="separate(game)"
      >
        Separate copy
      </button>
    </template>
    <template v-else-if="copies.length">
      <p>
        {{ members.length }} owned copies · {{ minutes.toLocaleString() }} min
        reported across copies
      </p>
      <p class="hint">
        Track overall status and notes on this main game. Open a copy for its
        own achievements, notes and files.
      </p>
      <ul>
        <li v-for="member in members" :key="member.id">
          <div>
            <RouterLink :to="`/games/${member.id}`"
              >{{ member.source || "Manual entry" }} ·
              {{
                member.platforms[0]?.platform || "Unspecified platform"
              }}</RouterLink
            >
            <span v-if="member.id === game.id" class="main-label"
              >Main entry</span
            >
            <p>{{ time(member) }} · {{ member.status }}</p>
            <p class="hint">{{ member.title }}</p>
          </div>
          <button
            v-if="member.id !== game.id"
            class="ui-btn ui-btn-secondary"
            type="button"
            :disabled="busy"
            :aria-label="`Separate ${member.source || member.title} copy`"
            @click="separate(member)"
          >
            Separate
          </button>
        </li>
      </ul>
    </template>
    <p v-else class="hint">
      Group another store or platform copy here to keep one main library entry.
    </p>
    <p v-if="error" class="copy-error" role="alert">{{ error }}</p>
    <GroupGameCopiesDialog
      v-if="choices"
      :games="choiceSummaries"
      :main-id="game.id"
      @close="choices = null"
      @grouped="grouped"
    />
  </section>
</template>

<style scoped>
.owned-copies {
  padding: 20px;
  border: 1px solid var(--ui-border);
  border-radius: 12px;
  margin-bottom: 24px;
}
header,
li {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 12px;
}
h2 {
  margin: 0;
  font-size: 1.15rem;
}
p {
  margin: 8px 0;
  line-height: 1.5;
}
.hint {
  color: var(--ui-dim);
  font-size: 0.9rem;
}
ul {
  list-style: none;
  padding: 0;
  margin: 12px 0 0;
}
li {
  padding: 12px 0;
  border-top: 1px solid var(--ui-border);
}
li > div {
  min-width: 0;
  flex: 1;
}
a,
p {
  overflow-wrap: anywhere;
}
a {
  color: var(--ui-accent-text);
}
.main-label {
  display: block;
  color: var(--ui-dim);
  font-size: 0.8rem;
  margin-top: 4px;
}
.copy-error {
  color: var(--ui-danger);
}
</style>
