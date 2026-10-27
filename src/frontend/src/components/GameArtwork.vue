<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { showGameCoverPlaceholder } from "../utils/gameImages";
import {
  cancelQueuedArtwork,
  currentArtworkScrollDirection,
  ensureArtworkScrollTracking,
  loadedArtworkSources,
  observeArtwork,
  queueArtwork,
  unobserveArtwork,
} from "../state/gameArtwork";

const props = withDefaults(
  defineProps<{
    src: string;
    title: string;
    placeholderKey?: string;
    eager?: boolean;
    fill?: boolean;
    fit?: "cover" | "contain";
  }>(),
  { eager: false, fill: false, fit: "cover" },
);

const requestedSrc = ref("");
const loaded = ref(false);
const priority = ref<"high" | "low">("low");
const failed = ref(false);
const host = ref<HTMLElement | null>(null);
const IMAGE_PREFETCH_MARGIN = 160;

// Give each game's fallback a stable, muted accent. Using its ID means duplicate
// titles still get distinct colors, and the choice stays fixed across renders.
const placeholderBackground = computed(() => {
  const gameId =
    props.placeholderKey ??
    props.src.match(
      /\/api\/game\/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\//i,
    )?.[1] ??
    props.title;
  let hash = 2166136261;
  for (let index = 0; index < gameId.length; index++) {
    hash ^= gameId.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  const palettes = [
    ["#16132a", "#3d2a68", "#9b87f5"],
    ["#102326", "#1f5960", "#65d6c7"],
    ["#26151b", "#633044", "#ef8aa8"],
    ["#201a12", "#66502b", "#e3bd69"],
    ["#151c2b", "#294a78", "#76a8ee"],
    ["#1b1722", "#4d315e", "#cf8fe8"],
  ];
  const [background, foreground, accent] =
    palettes[(hash >>> 0) % palettes.length];
  return `radial-gradient(ellipse at 50% 38%, ${accent}44, transparent 74%), linear-gradient(145deg, ${foreground}, ${background} 78%)`;
});

function requestImage(visible: boolean) {
  if (!props.src) return;
  priority.value = visible ? "high" : "low";
  requestedSrc.value = props.src;
  loaded.value = loadedArtworkSources.has(props.src);
}

function queueImage() {
  if (!host.value || requestedSrc.value) return;
  const source = props.src;
  queueArtwork(host.value, (visible) => {
    if (props.src !== source) return;
    requestImage(visible);
    unobserveArtwork(host.value);
  });
}

function isActuallyVisible() {
  const rect = host.value?.getBoundingClientRect();
  return Boolean(rect && rect.bottom > 0 && rect.top < window.innerHeight);
}

function onIntersect(entry: IntersectionObserverEntry) {
  if (!entry.isIntersecting) return;
  // Virtualized rows can move between IntersectionObserver's measurement and
  // its callback. Use the live card position so a stale entry cannot mark a
  // whole offscreen batch as visible/high priority.
  const rect = host.value?.getBoundingClientRect() ?? entry.boundingClientRect;
  const visible = rect.bottom > 0 && rect.top < window.innerHeight;
  const withinPrefetchRange =
    rect.bottom > -IMAGE_PREFETCH_MARGIN &&
    rect.top < window.innerHeight + IMAGE_PREFETCH_MARGIN;
  if (!withinPrefetchRange) return;
  // Do not start a request for an item that is already behind the user's
  // current direction of travel. It will be reconsidered if they reverse.
  const scrollDirection = currentArtworkScrollDirection();
  if (!visible && scrollDirection > 0 && rect.bottom <= 0) return;
  if (!visible && scrollDirection < 0 && rect.top >= window.innerHeight) return;
  queueImage();
}

function observe() {
  cancelQueuedArtwork(host.value);
  unobserveArtwork(host.value);
  requestedSrc.value = "";
  loaded.value = false;
  failed.value = false;
  if (!props.src) return;
  if (loadedArtworkSources.has(props.src) && isActuallyVisible()) {
    requestImage(isActuallyVisible());
    return;
  }
  if (!host.value || typeof IntersectionObserver === "undefined") {
    requestImage(false);
    return;
  }
  observeArtwork(host.value, onIntersect);
  // `eager` is only a priority hint. Keep observing in case a virtual row
  // moves away before the queued request starts, then comes back into view.
  if (props.eager && isActuallyVisible()) queueImage();
}

function onLoad() {
  if (failed.value) return;
  loaded.value = true;
  loadedArtworkSources.add(props.src);
}

function onError(event: Event) {
  failed.value = true;
  showGameCoverPlaceholder(event, props.title);
}

watch(() => props.src, observe);
watch(
  () => props.eager,
  (eager) => {
    if (eager && isActuallyVisible()) queueImage();
  },
);
onMounted(observe);
onMounted(ensureArtworkScrollTracking);
onUnmounted(() => {
  cancelQueuedArtwork(host.value);
  unobserveArtwork(host.value);
});
</script>

<template>
  <div
    ref="host"
    class="game-artwork"
    :class="{ 'is-fill': fill }"
    :style="{ background: placeholderBackground }"
  >
    <svg
      class="game-artwork__placeholder"
      viewBox="0 0 64 64"
      aria-hidden="true"
    >
      <path
        d="M17 24h30c6 0 9 4 10 11l2 13c1 6-6 9-10 4l-5-6H20l-5 6c-4 5-11 2-10-4l2-13c1-7 4-11 10-11Z"
        fill="none"
        stroke="currentColor"
        stroke-width="3"
        stroke-linejoin="round"
      />
      <path
        d="M19 31v12m-6-6h12m17-3h.1M49 39h.1"
        fill="none"
        stroke="currentColor"
        stroke-width="3"
        stroke-linecap="round"
      />
    </svg>
    <img
      v-if="requestedSrc"
      class="game-artwork__image"
      :class="{ 'is-loaded': loaded }"
      :src="requestedSrc"
      :alt="''"
      :style="{ objectFit: fit }"
      :loading="priority === 'high' ? 'eager' : 'lazy'"
      :fetchpriority="priority"
      decoding="async"
      @load="onLoad"
      @error="onError"
    />
  </div>
</template>

<style scoped>
.game-artwork {
  position: relative;
  width: 100%;
  height: auto;
  overflow: hidden;
}
.game-artwork.is-fill {
  height: 100%;
}
.game-artwork__placeholder {
  position: absolute;
  inset: 23%;
  width: 54%;
  height: 54%;
  color: #aeb7c2;
  filter: drop-shadow(0 2px 7px #0008);
}
.game-artwork__image {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  opacity: 0;
  transition: opacity 120ms ease-out;
}
.game-artwork__image.is-loaded {
  opacity: 1;
}
</style>
