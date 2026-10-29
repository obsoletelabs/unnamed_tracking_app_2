<script setup lang="ts">
// Where a game record came from: the store it claims, its platform and every
// connection that reports it (Playnite, a plugin, a store sync).
import { computed } from "vue";
import { storeName } from "../../utils/gameCopies";
import {
  cameFromPlaynite,
  reporterNames,
  type OriginGame,
} from "../../utils/gameOrigin";

const props = defineProps<{ game: OriginGame }>();
const store = computed(() => storeName(props.game.source) ?? "Added manually");
const platform = computed(() => props.game.platform?.trim() || null);
const reporters = computed(() =>
  reporterNames(props.game).filter((name) => name !== "Added manually"),
);
const playnite = computed(() => cameFromPlaynite(props.game));
</script>

<template>
  <span class="origin" role="group" aria-label="Where this record came from">
    <span class="tag store">{{ store }}</span>
    <span v-if="platform" class="tag">{{ platform }}</span>
    <span
      v-for="name in reporters"
      :key="name"
      class="tag via"
      :class="{ playnite: name === 'Playnite' && playnite }"
      :title="`Reported by ${name}`"
      >via {{ name }}</span
    >
  </span>
</template>

<style scoped>
.origin {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}
.tag {
  border: 1px solid var(--ui-border);
  border-radius: 999px;
  color: var(--ui-dim);
  font-size: 0.72rem;
  font-weight: 600;
  line-height: 1.5;
  padding: 1px 8px;
  white-space: nowrap;
}
.store {
  border-color: var(--ui-accent-line);
  background: var(--ui-accent-soft);
  color: var(--ui-text);
}
.via.playnite {
  border-color: var(--ui-info);
  background: var(--ui-info-soft);
  color: var(--ui-info);
}
</style>
