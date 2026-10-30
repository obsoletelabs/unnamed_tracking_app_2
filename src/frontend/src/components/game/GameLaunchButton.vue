<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, toRef, watch } from "vue";
import type { LaunchDevice } from "../../services/remoteLaunch";
import type { Game } from "../../types/game";
import { useRemoteLaunch } from "../../composables/useRemoteLaunch";
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

const remote = useRemoteLaunch(toRef(() => props.game.id));
const remoteEligible = computed(
  () =>
    props.game.status !== "wishlist" &&
    /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(
      props.game.playniteGuid ?? "",
    ),
);
const hasMenu = computed(
  () => targets.value.length > 1 || remoteEligible.value,
);
async function launchRemote(device: LaunchDevice) {
  menuOpen.value = false;
  await remote.launch(device);
}
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
  const below = window.innerHeight - box.bottom - 18;
  const height = menu.value?.offsetHeight ?? 180;
  const above = below < Math.min(height, 180) && box.top > below;
  const maxHeight = Math.min(480, Math.max(80, (above ? box.top : below) - 12));
  menuStyle.value = {
    top: `${above ? box.top - Math.min(height, maxHeight) - 6 : box.bottom + 6}px`,
    maxHeight: `${maxHeight}px`,
    left: `${Math.max(12, Math.min(box.left, window.innerWidth - (menu.value?.offsetWidth ?? box.width) - 12))}px`,
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

watch(menuOpen, async (open) => {
  listen(open);
  if (open) {
    placeMenu();
    if (remoteEligible.value) void remote.loadDevices();
    await nextTick();
    if (menuOpen.value) placeMenu();
  }
});
watch([remote.devices, remote.loading, remote.deviceError], async () => {
  if (menuOpen.value) {
    await nextTick();
    if (menuOpen.value) placeMenu();
  }
});
onBeforeUnmount(() => listen(false));
</script>

<template>
  <template v-if="primary">
    <div ref="root" class="launch-group">
      <button
        type="button"
        class="launch-btn launch-main"
        :class="{ 'has-menu': hasMenu }"
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
        v-if="hasMenu"
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
          <li
            v-if="remoteEligible"
            class="launch-section-heading"
            role="presentation"
          >
            On this device
          </li>
          <li v-for="target in targets" :key="target.id" role="none">
            <button type="button" role="menuitem" @click="run(target)">
              {{ targetLabel(target) }}
            </button>
          </li>
          <template v-if="remoteEligible">
            <li
              class="launch-section-heading launch-remote-heading"
              role="presentation"
            >
              Remote start in Playnite
            </li>
            <li class="launch-menu-caption" role="presentation">
              Starts the game on a connected PC.
            </li>
            <li
              v-if="remote.loading.value && !remote.devices.value.length"
              class="launch-help"
              role="presentation"
            >
              Loading computers…
            </li>
            <li
              v-else-if="remote.deviceError.value"
              class="launch-help"
              role="presentation"
            >
              {{ remote.deviceError.value }}
              <button type="button" role="menuitem" @click="remote.loadDevices">
                Retry loading computers
              </button>
            </li>
            <li
              v-for="device in remote.devices.value"
              :key="device.id"
              role="none"
            >
              <button
                type="button"
                role="menuitem"
                :disabled="!device.online || remote.busy.value"
                :aria-label="`Remote start on ${device.name}${device.online ? '' : ' (offline)'}`"
                class="launch-device"
                @click="launchRemote(device)"
              >
                <span>{{ device.name }}</span>
                <span
                  class="launch-device-state"
                  :class="{ online: device.online }"
                  >{{ device.online ? "Online" : "Offline" }}</span
                >
              </button>
            </li>
            <li
              v-if="
                !remote.loading.value &&
                !remote.deviceError.value &&
                !remote.devices.value.length
              "
              class="launch-help"
              role="presentation"
            >
              Enable remote launch in the Playnite extension on your PC, then
              sync this game.
            </li>
          </template>
        </ul>
      </Teleport>
    </div>
    <div
      v-if="remote.message.value || remote.error.value"
      class="launch-status"
      role="status"
      aria-live="polite"
    >
      <strong class="launch-status-title">Remote start</strong>
      <span>{{ remote.message.value }}</span>
      <span v-if="remote.error.value">{{ remote.error.value }}</span>
      <button v-if="remote.retryable.value" type="button" @click="remote.retry">
        Check this request
      </button>
    </div>
  </template>
</template>

<style scoped>
.launch-group {
  position: relative;
  display: inline-flex;
  flex-wrap: wrap;
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
  background: var(--ui-surface, #fff);
  color: var(--ui-text, #1f1f1f);
  border: 1px solid var(--ui-border, rgb(0 0 0 / 15%));
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
.launch-section-heading {
  padding: 8px 12px 4px;
  color: var(--ui-dim, #666);
  font-size: 0.75rem;
  font-weight: 600;
}
.launch-remote-heading {
  margin-top: 6px;
  padding-top: 12px;
  border-top: 1px solid var(--ui-border, #8884);
}
.launch-menu-caption {
  padding: 0 12px 6px;
  color: var(--ui-dim, #666);
  font-size: 0.8rem;
}
.launch-menu .launch-device {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}
.launch-device-state {
  flex-shrink: 0;
  color: var(--ui-dim, #666);
  font-size: 0.75rem;
}
.launch-device-state.online {
  color: var(--ui-good, #26734d);
}
.launch-help {
  max-width: min(320px, 80vw);
  padding: 8px 12px;
  white-space: normal;
}
.launch-menu {
  max-width: calc(100vw - 24px);
  max-height: min(480px, 75vh);
  overflow-y: auto;
}
.launch-menu button {
  white-space: normal;
  overflow-wrap: anywhere;
}
.launch-menu button:disabled {
  opacity: 0.6;
  cursor: default;
}
.launch-status {
  order: 1;
  flex: 0 0 100%;
  box-sizing: border-box;
  min-width: 0;
  padding: 12px 14px;
  border: 1px solid var(--ui-border, #8884);
  border-radius: var(--ui-radius-control, 8px);
  background: var(--ui-surface, #fff);
  font-size: 0.85rem;
  white-space: normal;
}
.launch-status-title {
  display: block;
  margin-bottom: 4px;
  font-size: 0.8rem;
}
.launch-status span {
  display: block;
  max-width: 72ch;
  overflow-wrap: anywhere;
}
.launch-status button {
  margin-top: 6px;
  color: inherit;
  background: transparent;
  border: 1px solid currentColor;
  border-radius: 6px;
  padding: 5px 8px;
}
</style>
