<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import type { Game } from "../../types/game";
import { fetchIdentitySources } from "../../services/gameIdentity";
import {
  defaultTarget,
  launchTargets,
  targetLabel,
  type LaunchObservation,
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
const observations = ref<LaunchObservation[]>([]);
const targets = computed(() => launchTargets(props.game, observations.value));
const primary = computed(() =>
  defaultTarget(targets.value, props.game, remembered.value),
);
// Library imports keep their store IDs on the game's sources; a failed lookup just means fewer options.
watch(
  () => props.game.id,
  async (id, _previous, onCleanup) => {
    const request = new AbortController();
    onCleanup(() => request.abort());
    try {
      const data = await fetchIdentitySources(id, request.signal);
      observations.value = data.observations.map((item) => ({
        source: item.source,
        externalId: item.external_id,
        available: item.availability === "available",
      }));
    } catch {
      observations.value = [];
    }
  },
  { immediate: true },
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

const menu = ref<HTMLElement | null>(null);
const menuStyle = ref<Record<string, string>>({});

// The menu is rendered in the page body so the hero section cannot clip it; this places it under
// the button.
function placeMenu() {
  const box = root.value?.getBoundingClientRect();
  if (!box) return;
  menuStyle.value = {
    top: `${box.bottom + 6}px`,
    left: `${box.left}px`,
    minWidth: `${box.width}px`,
  };
}

function closeOnOutside(event: Event) {
  const inside = (node: HTMLElement | null) =>
    node?.contains(event.target as Node) ?? false;
  if (!inside(root.value) && !inside(menu.value)) menuOpen.value = false;
}

function closeOnEscape(event: Event) {
  if ((event as KeyboardEvent).key === "Escape") menuOpen.value = false;
}

function close() {
  menuOpen.value = false;
}

function listen(add: boolean) {
  const method = add ? "addEventListener" : "removeEventListener";
  document[method]("click", closeOnOutside);
  document[method]("keydown", closeOnEscape);
  window[method]("resize", close);
  window[method]("scroll", close, true);
}

watch(menuOpen, (open) => {
  if (open) placeMenu();
  listen(open);
});
onBeforeUnmount(() => listen(false));
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
    <Teleport to="body">
      <ul
        v-if="menuOpen"
        ref="menu"
        class="launch-menu"
        role="menu"
        :style="menuStyle"
      >
        <li v-for="target in targets" :key="target.id" role="none">
          <button type="button" role="menuitem" @click="run(target)">
            {{ targetLabel(target) }}
          </button>
        </li>
      </ul>
    </Teleport>
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
  position: fixed;
  z-index: 1000;
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
