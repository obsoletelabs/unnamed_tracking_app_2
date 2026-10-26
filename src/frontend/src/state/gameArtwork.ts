/** Shared page-lifetime state for artwork cards.
 *
 * GameArtwork uses <script setup>, so mutable values declared there belong to
 * each card instance. Keep the loaded URL set and scroll-direction listener in
 * this ordinary module so a library with thousands of cards still has one
 * shared set and one window scroll listener.
 */

export const loadedArtworkSources = new Set<string>();

interface PendingArtwork {
  element: HTMLElement;
  start: (visible: boolean) => void;
}

const pendingArtwork = new Map<HTMLElement, PendingArtwork>();
const PREFETCH_MARGIN = 160;
const intersections = new Map<
  HTMLElement,
  (entry: IntersectionObserverEntry) => void
>();
let artworkObserver: IntersectionObserver | null = null;

export function observeArtwork(
  element: HTMLElement,
  onIntersect: (entry: IntersectionObserverEntry) => void,
): void {
  if (!artworkObserver) {
    artworkObserver = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          intersections.get(entry.target as HTMLElement)?.(entry);
        }
      },
      { rootMargin: `${PREFETCH_MARGIN}px 0px`, threshold: 0 },
    );
  }
  intersections.set(element, onIntersect);
  artworkObserver.observe(element);
}

export function unobserveArtwork(element: HTMLElement | null): void {
  if (!element) return;
  intersections.delete(element);
  artworkObserver?.unobserve(element);
}
// Spread DOM updates across frames so Vue and the browser start the nearest
// images before the rest of the visible cards. A large grid can otherwise
// hand every image URL to the browser in DOM order in a single update.
const ARTWORK_PER_FRAME = 8;
let artworkFrame = 0;

function scheduleArtwork(): void {
  if (artworkFrame || !pendingArtwork.size) return;
  artworkFrame = requestAnimationFrame(flushArtwork);
}

function flushArtwork(): void {
  artworkFrame = 0;
  const viewportMiddle = window.innerHeight / 2;
  const candidates = [...pendingArtwork.values()]
    .map((item) => ({ item, rect: item.element.getBoundingClientRect() }))
    .filter(({ item, rect }) => {
      if (
        !item.element.isConnected ||
        rect.bottom <= -PREFETCH_MARGIN ||
        rect.top >= window.innerHeight + PREFETCH_MARGIN
      ) {
        pendingArtwork.delete(item.element);
        return false;
      }
      return true;
    })
    .sort((a, b) => {
      const aDistance = Math.abs(
        (a.rect.top + a.rect.bottom) / 2 - viewportMiddle,
      );
      const bDistance = Math.abs(
        (b.rect.top + b.rect.bottom) / 2 - viewportMiddle,
      );
      return aDistance - bDistance;
    });

  for (const { item, rect } of candidates.slice(0, ARTWORK_PER_FRAME)) {
    if (pendingArtwork.get(item.element) !== item) continue;
    pendingArtwork.delete(item.element);
    item.start(rect.bottom > 0 && rect.top < window.innerHeight);
  }
  scheduleArtwork();
}

export function queueArtwork(
  element: HTMLElement,
  start: (visible: boolean) => void,
): void {
  pendingArtwork.set(element, { element, start });
  scheduleArtwork();
}

export function cancelQueuedArtwork(element: HTMLElement | null): void {
  if (element) pendingArtwork.delete(element);
}

let lastScrollY = 0;
let scrollDirection: 1 | -1 = 1;
let scrollTrackerReady = false;

export function currentArtworkScrollDirection(): 1 | -1 {
  return scrollDirection;
}

export function ensureArtworkScrollTracking(): void {
  if (scrollTrackerReady || typeof window === "undefined") return;
  scrollTrackerReady = true;
  lastScrollY = window.scrollY;
  window.addEventListener(
    "scroll",
    () => {
      const next = window.scrollY;
      if (next !== lastScrollY) scrollDirection = next > lastScrollY ? 1 : -1;
      lastScrollY = next;
    },
    { passive: true },
  );
}
