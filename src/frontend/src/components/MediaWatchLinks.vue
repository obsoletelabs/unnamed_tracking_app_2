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
      v-for="(link, index) in links"
      :key="link.id"
      :class="index === 0 ? 'watch-main' : 'watch-other'"
      :href="link.url"
      target="_blank"
      rel="noopener noreferrer"
    >
      {{ index === 0 ? "▶ " : "" }}Watch in {{ link.label }}
    </a>
  </div>
</template>

<style scoped>
.watch-links {
  display: inline-flex;
  flex-wrap: wrap;
  gap: 8px;
}
.watch-main,
.watch-other {
  display: inline-flex;
  align-items: center;
  height: 38px;
  padding: 0 18px;
  border-radius: var(--ui-radius-control);
  font-size: 0.86rem;
  font-weight: 700;
  text-decoration: none;
  white-space: nowrap;
}
.watch-main {
  background: var(--ui-accent);
  color: var(--ui-on-accent);
}
.watch-other {
  color: var(--ui-text);
  border: 1px solid color-mix(in srgb, var(--ui-text) 22%, transparent);
}
.watch-other:hover {
  border-color: color-mix(in srgb, var(--ui-accent) 55%, transparent);
}
</style>
