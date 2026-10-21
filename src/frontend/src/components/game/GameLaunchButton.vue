<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import type { Game } from "../../types/game";
import {
  defaultTarget,
  launchTargets,
  targetLabel,
  type LaunchTarget,
} from "../../utils/gameLaunch";

const props = defineProps<{ game: Game }>();
const STORAGE_KEY = "launch-client";

function readRemembered(): string | null {
  try {
    return window.localStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}

const remembered = ref<string | null>(readRemembered());
const targets = computed(() => launchTargets(props.game));
const primary = computed(() =>
  defaultTarget(targets.value, props.game, remembered.value),
);
const menuOpen = ref(false);
const root = ref<HTMLElement | null>(null);

// The browser hands app links to the operating system, which asks for confirmation the first
// time. Nothing is sent to this server. Store pages are ordinary web links.
function run(target: LaunchTarget) {
  menuOpen.value = false;
  if (target.action === "store") {
    window.open(target.uri, "_blank", "noopener,noreferrer");
    return;
  }
  remembered.value = target.id;
  try {
    window.localStorage.setItem(STORAGE_KEY, target.id);
  } catch {
    // The choice just is not remembered.
  }
  window.location.assign(target.uri);
}

function closeOnOutside(event: Event) {
  if (root.value && !root.value.contains(event.target as Node)) {
    menuOpen.value = false;
  }
}

function closeOnEscape(event: KeyboardEvent) {
  if (event.key === "Escape") menuOpen.value = false;
}

watch(menuOpen, (open) => {
  if (open) {
    document.addEventListener("click", closeOnOutside);
    document.addEventListener("keydown", closeOnEscape);
  } else {
    document.removeEventListener("click", closeOnOutside);
    document.removeEventListener("keydown", closeOnEscape);
  }
});
onBeforeUnmount(() => {
  document.removeEventListener("click", closeOnOutside);
  document.removeEventListener("keydown", closeOnEscape);
});
</script>

<template>
  <div v-if="primary" ref="root" class="launch-group">
    <button
      type="button"
      class="launch-btn launch-main"
      :class="{ 'has-menu': targets.length > 1 }"
      :title="
        primary.action === 'store'
          ? 'Opens the store page in a new tab.'
          : `Opens ${primary.label} on this device. Install it and allow the browser prompt if nothing happens.`
      "
      @click="run(primary)"
    >
      {{ primary.action === "store" ? "↗" : "▶" }} {{ targetLabel(primary) }}
    </button>
    <button
      v-if="targets.length > 1"
      type="button"
      class="launch-btn launch-toggle"
      aria-haspopup="menu"
      :aria-expanded="menuOpen"
      aria-label="More ways to launch"
      @click="menuOpen = !menuOpen"
    >
      ▾
    </button>
    <ul v-if="menuOpen" class="launch-menu" role="menu">
      <li v-for="target in targets" :key="target.id" role="none">
        <button type="button" role="menuitem" @click="run(target)">
          {{ targetLabel(target) }}
        </button>
      </li>
    </ul>
  </div>
</template>

<style scoped>
.launch-group {
  position: relative;
  display: inline-flex;
}
.launch-btn {
  padding: 8px 14px;
  border: 0;
  background: var(--accent, #6d5efc);
  color: var(--accent-contrast, #fff);
  font-weight: 600;
  cursor: pointer;
}
.launch-main {
  border-radius: 8px;
}
.launch-main.has-menu {
  border-radius: 8px 0 0 8px;
}
.launch-toggle {
  padding: 8px 10px;
  border-left: 1px solid rgb(255 255 255 / 35%);
  border-radius: 0 8px 8px 0;
}
.launch-btn:hover {
  filter: brightness(1.08);
}
.launch-btn:focus-visible,
.launch-menu button:focus-visible {
  outline: 2px solid currentColor;
  outline-offset: 2px;
}
.launch-menu {
  position: absolute;
  top: calc(100% + 6px);
  left: 0;
  z-index: 20;
  min-width: 100%;
  margin: 0;
  padding: 6px;
  list-style: none;
  background: var(--surface, #fff);
  color: var(--text, #1f1f1f);
  border: 1px solid var(--border, rgb(0 0 0 / 15%));
  border-radius: 10px;
  box-shadow: 0 8px 24px rgb(0 0 0 / 18%);
}
.launch-menu button {
  width: 100%;
  padding: 8px 12px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: inherit;
  text-align: left;
  white-space: nowrap;
  cursor: pointer;
}
.launch-menu button:hover {
  background: var(--surface-hover, rgb(0 0 0 / 7%));
}
</style>
