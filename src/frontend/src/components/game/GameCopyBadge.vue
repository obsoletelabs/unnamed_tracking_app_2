<script setup lang="ts">
// Shows which copy of a merged game an item belongs to, e.g. a Playnite save
// from the Steam copy listed on the main copy's page (#274).
import { computed } from "vue";
import type { GameCopyLabel } from "../../types/game";
import { copyBadge } from "../../utils/gameCopies";

const props = defineProps<{ copy?: GameCopyLabel }>();
const label = computed(() => copyBadge(props.copy));
</script>

<template>
  <span
    v-if="label"
    class="copy-badge"
    :title="`From your ${label} copy of ${copy!.title}`"
  >
    {{ label }}
  </span>
</template>

<style scoped>
.copy-badge {
  align-self: flex-start;
  width: fit-content;
  display: inline-flex;
  align-items: center;
  max-width: 100%;
  padding: 1px 8px;
  border: 1px solid var(--ui-accent-line);
  border-radius: 999px;
  background: var(--ui-accent-soft);
  color: var(--ui-text);
  font-size: 0.72rem;
  font-weight: 600;
  line-height: 1.5;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
</style>
