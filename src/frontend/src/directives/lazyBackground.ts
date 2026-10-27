// A background image that is only fetched once its element is about to scroll
// into view. Used for picture tiles that are CSS backgrounds (so they cannot
// use <img loading="lazy">): v-lazy-bg="url".
import type { Directive } from "vue";
import {
  cancelQueuedArtwork,
  loadedArtworkSources,
  observeArtwork,
  queueArtwork,
  unobserveArtwork,
} from "../state/gameArtwork";

function apply(el: HTMLElement, url: string | null | undefined) {
  cancelQueuedArtwork(el);
  unobserveArtwork(el);
  if (!url) {
    delete el.dataset.lazyBg;
    el.style.backgroundImage = "";
    return;
  }
  if (typeof IntersectionObserver === "undefined") {
    el.style.backgroundImage = `url(${url})`;
    return;
  }
  el.dataset.lazyBg = url;
  el.style.backgroundImage = "";
  observeArtwork(el, (entry) => {
    if (!entry.isIntersecting) return;
    queueArtwork(el, () => {
      if (el.dataset.lazyBg !== url) return;
      el.style.backgroundImage = `url(${url})`;
      unobserveArtwork(el);
    });
  });
}

export const vLazyBg: Directive<HTMLElement, string | null | undefined> = {
  mounted: (el, binding) => apply(el, binding.value),
  updated: (el, binding) => {
    if (binding.value !== binding.oldValue) apply(el, binding.value);
  },
  unmounted: (el) => {
    cancelQueuedArtwork(el);
    unobserveArtwork(el);
  },
};

const imageLoads = new WeakMap<HTMLImageElement, () => void>();
function applyImage(el: HTMLImageElement, source: string | null | undefined) {
  cancelQueuedArtwork(el);
  unobserveArtwork(el);
  const previousLoad = imageLoads.get(el);
  if (previousLoad) el.removeEventListener("load", previousLoad);
  el.removeAttribute("src");
  el.dataset.viewportSource = source ?? "";
  if (!source) return;
  const loaded = () => {
    if (el.currentSrc === new URL(source, document.baseURI).href)
      loadedArtworkSources.add(source);
  };
  imageLoads.set(el, loaded);
  el.addEventListener("load", loaded);
  const start = (visible: boolean) => {
    if (el.dataset.viewportSource !== source) return;
    el.loading = visible ? "eager" : "lazy";
    el.fetchPriority = visible ? "high" : "low";
    el.src = source;
    unobserveArtwork(el);
  };
  const rect = el.getBoundingClientRect();
  const visible = rect.bottom > 0 && rect.top < window.innerHeight;
  if (
    typeof IntersectionObserver === "undefined" ||
    (visible && loadedArtworkSources.has(source))
  ) {
    start(visible);
    return;
  }
  observeArtwork(el, (entry) => {
    if (entry.isIntersecting) queueArtwork(el, start);
  });
}

// Media library posters share the centre-first request queue with Games.
// Once assigned, an image keeps its src when it scrolls outside the viewport.
export const vViewportImage: Directive<
  HTMLImageElement,
  string | null | undefined
> = {
  mounted: (el, binding) => applyImage(el, binding.value),
  updated: (el, binding) => {
    if (binding.value !== binding.oldValue) applyImage(el, binding.value);
  },
  unmounted: (el) => {
    cancelQueuedArtwork(el);
    unobserveArtwork(el);
    const loaded = imageLoads.get(el);
    if (loaded) el.removeEventListener("load", loaded);
    imageLoads.delete(el);
  },
};
