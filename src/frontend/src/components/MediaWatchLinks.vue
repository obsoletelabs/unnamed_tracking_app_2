<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import type { MediaType } from "../services/mediaExtras";
import {
  defaultWatchLink,
  fetchWatchLinks,
  type WatchLink,
} from "../services/mediaWatchLinks";
import { providerLinks } from "../utils/mediaProviderLinks";

const props = defineProps<{
  mediaType: MediaType;
  mediaId: string;
  providerIds?: Record<string, string>;
}>();
const emit = defineEmits<{ present: [value: boolean] }>();

const watchSources = ref<WatchLink[]>([]);
const sites = computed(() => providerLinks(props.mediaType, props.providerIds));
const STORAGE_KEY = "watch-source";

function readRemembered(): string | null {
  try {
    return window.localStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}

// The source the person used last wins, else the one that most recently reported it played.
const remembered = ref<string | null>(readRemembered());
const primary = computed(() =>
  defaultWatchLink(watchSources.value, remembered.value),
);
// The button: a media server that has the title wins; otherwise the first site to open it in
// (TMDB, AniList...), the way a game's button falls back to its store page.
const main = computed(() => {
  if (primary.value) {
    return {
      url: primary.value.url,
      text: `▶ Watch in ${primary.value.label}`,
      source: primary.value.id,
    };
  }
  const site = sites.value[0];
  return site
    ? { url: site.url, text: `↗ Open in ${site.label}`, source: null }
    : null;
});
// Like the game launcher's menu, this lists every choice, including the one on the button.
const menuItems = computed(() => [
  ...watchSources.value.map((link) => ({
    id: `watch:${link.id}`,
    text: `Watch in ${link.label}`,
    url: link.url,
    source: link.id,
    site: false,
  })),
  ...sites.value.map((link) => ({
    id: `open:${link.id}`,
    text: `Open in ${link.label}`,
    url: link.url,
    source: null,
    site: true,
  })),
]);

function remember(source: string | null) {
  if (!source) return;
  remembered.value = source;
  try {
    window.localStorage.setItem(STORAGE_KEY, source);
  } catch {
    // The choice just is not remembered.
  }
}

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
  },
  { immediate: true },
);
// Edit steps back whenever there is a button to press, whatever it opens.
watch(
  () => main.value !== null,
  (value) => emit("present", value),
  {
    immediate: true,
  },
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
  <div v-if="main" ref="root" class="watch-links">
    <a
      class="watch-main"
      :class="{ 'has-menu': menuItems.length > 1 }"
      :href="main.url"
      target="_blank"
      rel="noopener noreferrer"
      @click="remember(main.source)"
    >
      {{ main.text }}
    </a>
    <button
      v-if="menuItems.length > 1"
      type="button"
      class="watch-toggle"
      aria-haspopup="menu"
      :aria-expanded="menuOpen"
      aria-label="More places to watch or open"
      @click="menuOpen = !menuOpen"
    >
      ▾
    </button>
    <Teleport to="body">
      <ul
        v-if="menuOpen"
        ref="menu"
        class="watch-menu"
        role="menu"
        :style="menuStyle"
      >
        <li
          v-for="item in menuItems"
          :key="item.id"
          role="none"
          :class="{ site: item.site }"
        >
          <a
            role="menuitem"
            :href="item.url"
            target="_blank"
            rel="noopener noreferrer"
            @click="
              remember(item.source);
              close();
            "
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
.watch-menu li.site:first-of-type,
.watch-menu li:not(.site) + li.site {
  margin-top: 4px;
  padding-top: 4px;
  border-top: 1px solid
    color-mix(in srgb, var(--ui-text, #000) 12%, transparent);
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
