<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import type { MediaType } from "../services/mediaExtras";
import { fetchWatchLinks, type WatchLink } from "../services/mediaWatchLinks";
import { providerLinks } from "../utils/mediaProviderLinks";

const props = defineProps<{
  mediaType: MediaType;
  mediaId: string;
  providerIds?: Record<string, string>;
}>();
const emit = defineEmits<{ present: [value: boolean] }>();

const watchSources = ref<WatchLink[]>([]);
const sites = computed(() => providerLinks(props.mediaType, props.providerIds));
// The main button is the first place the title can be watched; everything else is in the menu.
const primary = computed(() => watchSources.value[0] ?? null);
const menuItems = computed(() => [
  ...watchSources.value.slice(1).map((link) => ({
    id: `watch:${link.id}`,
    text: `Watch in ${link.label}`,
    url: link.url,
  })),
  ...sites.value.map((link) => ({
    id: `open:${link.id}`,
    text: `Open in ${link.label}`,
    url: link.url,
  })),
]);

// The links were saved by the library sync, so this is one cheap read; a failure just means no button.
watch(
  () => [props.mediaType, props.mediaId],
  async (_value, _previous, onCleanup) => {
    const request = new AbortController();
    onCleanup(() => request.abort());
    try {
      watchSources.value = await fetchWatchLinks(
        props.mediaType,
        props.mediaId,
        request.signal,
      );
    } catch {
      watchSources.value = [];
    }
    emit("present", watchSources.value.length > 0);
  },
  { immediate: true },
);

const menuOpen = ref(false);
const root = ref<HTMLElement | null>(null);
const menu = ref<HTMLElement | null>(null);
const menuStyle = ref<Record<string, string>>({});

// The menu is rendered in the page body so the hero banner cannot clip it.
function placeMenu() {
  const box = root.value?.getBoundingClientRect();
  if (!box) return;
  menuStyle.value = {
    top: `${box.bottom + 6}px`,
    left: `${box.left}px`,
    minWidth: `${box.width}px`,
  };
}

function close() {
  menuOpen.value = false;
}

function closeOnOutside(event: Event) {
  const inside = (node: HTMLElement | null) =>
    node?.contains(event.target as Node) ?? false;
  if (!inside(root.value) && !inside(menu.value)) close();
}

function closeOnEscape(event: Event) {
  if ((event as KeyboardEvent).key === "Escape") close();
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
  <div v-if="primary || menuItems.length" ref="root" class="watch-links">
    <a
      v-if="primary"
      class="watch-main"
      :class="{ 'has-menu': menuItems.length }"
      :href="primary.url"
      target="_blank"
      rel="noopener noreferrer"
    >
      ▶ Watch in {{ primary.label }}
    </a>
    <button
      v-if="menuItems.length"
      type="button"
      class="watch-toggle"
      :class="{ alone: !primary }"
      aria-haspopup="menu"
      :aria-expanded="menuOpen"
      :aria-label="primary ? 'More places to watch or open' : 'Open in'"
      @click="menuOpen = !menuOpen"
    >
      {{ primary ? "▾" : "Open in ▾" }}
    </button>
    <Teleport to="body">
      <ul
        v-if="menuOpen"
        ref="menu"
        class="watch-menu"
        role="menu"
        :style="menuStyle"
      >
        <li v-for="item in menuItems" :key="item.id" role="none">
          <a
            role="menuitem"
            :href="item.url"
            target="_blank"
            rel="noopener noreferrer"
            @click="close"
            >{{ item.text }}</a
          >
        </li>
      </ul>
    </Teleport>
  </div>
</template>

<style scoped>
.watch-links {
  display: inline-flex;
}
.watch-main,
.watch-toggle {
  display: inline-flex;
  align-items: center;
  height: 38px;
  border: 0;
  font: inherit;
  font-size: 0.86rem;
  font-weight: 700;
  text-decoration: none;
  white-space: nowrap;
  cursor: pointer;
  background: var(--ui-accent);
  color: var(--ui-on-accent);
}
.watch-main {
  padding: 0 18px;
  border-radius: var(--ui-radius-control);
}
.watch-main.has-menu {
  border-radius: var(--ui-radius-control) 0 0 var(--ui-radius-control);
}
.watch-toggle {
  padding: 0 10px;
  border-left: 1px solid rgb(255 255 255 / 35%);
  border-radius: 0 var(--ui-radius-control) var(--ui-radius-control) 0;
}
.watch-toggle.alone {
  padding: 0 16px;
  border: 1px solid color-mix(in srgb, var(--ui-text) 22%, transparent);
  border-radius: var(--ui-radius-control);
  background: transparent;
  color: var(--ui-text);
}
.watch-main:focus-visible,
.watch-toggle:focus-visible {
  outline: 2px solid currentColor;
  outline-offset: 2px;
}
</style>

<style>
.watch-menu {
  position: fixed;
  z-index: 1000;
  margin: 0;
  padding: 6px;
  list-style: none;
  background: var(--ui-surface, #fff);
  color: var(--ui-text, #1f1f1f);
  border: 1px solid color-mix(in srgb, var(--ui-text, #000) 15%, transparent);
  border-radius: 10px;
  box-shadow: 0 8px 24px rgb(0 0 0 / 18%);
}
.watch-menu a {
  display: block;
  padding: 8px 10px;
  border-radius: 6px;
  color: inherit;
  text-decoration: none;
  white-space: nowrap;
}
.watch-menu a:hover,
.watch-menu a:focus-visible {
  background: color-mix(in srgb, var(--ui-accent) 14%, transparent);
}
</style>
