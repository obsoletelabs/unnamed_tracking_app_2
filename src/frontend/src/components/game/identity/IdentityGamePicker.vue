<script setup lang="ts">
import { computed, onMounted, ref, useId } from "vue";
import { fetchGames } from "../../../services/games";
import type { Game } from "../../../types/game";

const props = defineProps<{
  label: string;
  modelValue: string;
  exclude: string[];
}>();
const emit = defineEmits<{ "update:modelValue": [id: string] }>();

const games = ref<Game[]>([]);
const query = ref("");
const failed = ref(false);
const id = useId();

onMounted(async () => {
  try {
    games.value = await fetchGames();
  } catch {
    failed.value = true;
  }
});

const choices = computed(() => {
  const q = query.value.trim().toLowerCase();
  return games.value
    .filter((g) => !props.exclude.includes(g.id))
    .filter((g) => !q || g.title.toLowerCase().includes(q))
    .slice(0, 100);
});
function describe(game: Game): string {
  const platform = game.platforms[0]?.platform;
  return platform ? `${game.title} (${platform})` : game.title;
}
</script>

<template>
  <div class="picker">
    <label :for="`${id}-q`">{{ label }}</label>
    <input
      :id="`${id}-q`"
      v-model="query"
      class="ui-field"
      type="search"
      placeholder="Filter your games"
      autocomplete="off"
    />
    <select
      :id="`${id}-s`"
      class="ui-field"
      :aria-label="`${label}: choose a game`"
      :value="modelValue"
      @change="
        emit('update:modelValue', ($event.target as HTMLSelectElement).value)
      "
    >
      <option value="">Choose a game…</option>
      <option v-for="g in choices" :key="g.id" :value="g.id">
        {{ describe(g) }}
      </option>
    </select>
    <p v-if="failed" class="err" role="alert">Could not load your games.</p>
  </div>
</template>

<style scoped>
.picker {
  display: grid;
  gap: 6px;
  min-width: 0;
}
label {
  font-size: 0.85rem;
  color: var(--ui-dim);
}
.err {
  color: var(--ui-danger);
  margin: 0;
}
</style>
