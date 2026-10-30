<script setup lang="ts">
import { ref, watch } from "vue";
import type { MediaType } from "../services/mediaExtras";
import { fetchWatchLinks, type WatchLink } from "../services/mediaWatchLinks";

const props = defineProps<{ mediaType: MediaType; mediaId: string }>();
const emit = defineEmits<{ present: [value: boolean] }>();

const links = ref<WatchLink[]>([]);

// The links were saved by the library sync, so this is one cheap read; a failure just means no button.
watch(
  () => [props.mediaType, props.mediaId],
  async (_value, _previous, onCleanup) => {
    const request = new AbortController();
    onCleanup(() => request.abort());
    try {
      links.value = await fetchWatchLinks(
        props.mediaType,
        props.mediaId,
        request.signal,
      );
    } catch {
      links.value = [];
    }
    emit("present", links.value.length > 0);
  },
  { immediate: true },
);
</script>

<template>
  <div v-if="links.length" class="watch-links">
    <a
      class="watch-main"
      :href="links[0].url"
      target="_blank"
      rel="noopener noreferrer"
    >
      ▶ Watch in {{ links[0].label }}
    </a>
    <details v-if="links.length > 1" class="watch-more">
      <summary aria-label="More places to watch">▾</summary>
      <ul>
        <li v-for="link in links.slice(1)" :key="link.id">
          <a :href="link.url" target="_blank" rel="noopener noreferrer"
            >Watch in {{ link.label }}</a
          >
        </li>
      </ul>
    </details>
  </div>
</template>

<style scoped>
.watch-links {
  position: relative;
  display: inline-flex;
}
.watch-main,
.watch-more summary {
  display: inline-flex;
  align-items: center;
  height: 38px;
  padding: 0 18px;
  background: var(--ui-accent);
  color: var(--ui-on-accent);
  font-size: 0.86rem;
  font-weight: 700;
  text-decoration: none;
  white-space: nowrap;
  cursor: pointer;
  list-style: none;
}
.watch-main {
  border-radius: var(--ui-radius-control);
}
.watch-links:has(.watch-more) .watch-main {
  border-radius: var(--ui-radius-control) 0 0 var(--ui-radius-control);
}
.watch-more summary {
  padding: 0 10px;
  border-left: 1px solid rgb(255 255 255 / 35%);
  border-radius: 0 var(--ui-radius-control) var(--ui-radius-control) 0;
}
.watch-more summary::-webkit-details-marker {
  display: none;
}
.watch-more ul {
  position: absolute;
  z-index: 20;
  top: 44px;
  left: 0;
  min-width: 100%;
  margin: 0;
  padding: 6px;
  list-style: none;
  background: var(--ui-surface, #fff);
  border: 1px solid color-mix(in srgb, var(--ui-text) 15%, transparent);
  border-radius: 10px;
  box-shadow: 0 8px 24px rgb(0 0 0 / 18%);
}
.watch-more li a {
  display: block;
  padding: 8px 10px;
  color: var(--ui-text);
  text-decoration: none;
  white-space: nowrap;
}
.watch-more li a:hover {
  background: color-mix(in srgb, var(--ui-accent) 12%, transparent);
}
</style>
