<script setup lang="ts">
import { computed } from "vue";
import type { Game } from "../../types/game";
import { launchTargets, type LaunchTarget } from "../../utils/gameLaunch";

const props = defineProps<{ game: Game }>();
const targets = computed(() => launchTargets(props.game));

function buttonLabel(target: LaunchTarget): string {
  if (target.id === "gog") return `Open in ${target.label}`;
  return targets.value.length > 1 ? `Launch in ${target.label}` : "Launch";
}

// The browser hands the link to the operating system, which asks for confirmation the first
// time. Nothing is sent to this server.
function launch(uri: string) {
  window.location.assign(uri);
}
</script>

<template>
  <div
    v-if="targets.length"
    class="launch-group"
    role="group"
    aria-label="Launch game"
  >
    <button
      v-for="target in targets"
      :key="target.id"
      type="button"
      class="launch-btn"
      :title="`Opens ${target.label} on this device. Install it and allow the browser prompt if nothing happens.`"
      @click="launch(target.uri)"
    >
      ▶ {{ buttonLabel(target) }}
    </button>
  </div>
</template>

<style scoped>
.launch-group {
  display: inline-flex;
  flex-wrap: wrap;
  gap: 8px;
}
.launch-btn {
  padding: 8px 14px;
  border: 0;
  border-radius: 8px;
  background: var(--accent, #6d5efc);
  color: var(--accent-contrast, #fff);
  font-weight: 600;
  cursor: pointer;
}
.launch-btn:hover {
  filter: brightness(1.08);
}
.launch-btn:focus-visible {
  outline: 2px solid currentColor;
  outline-offset: 2px;
}
</style>
