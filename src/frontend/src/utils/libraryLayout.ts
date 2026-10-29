import type { Virtualizer } from "@tanstack/vue-virtual";

// Phone density stays distinct across Games, Media shelf and Media board.
export const MIN_CARD_WIDTH = { compact: 150, cozy: 200, large: 260 } as const;
export const PHONE_CARD_COLUMNS = { compact: 3, cozy: 2, large: 1 } as const;

// Vue can invoke a row ref before its DOM has a measurable height. Keep the
// cached/estimated size until ResizeObserver delivers a real border box, rather
// than caching zero and stacking every buffered row in the visible viewport.
export function measureLibraryRow(
  element: Element,
  entry: ResizeObserverEntry | undefined,
  instance: Virtualizer<Window, Element>,
): number {
  const measured = entry
    ? (entry.borderBoxSize?.[0]?.blockSize ??
      element.getBoundingClientRect().height)
    : 0;
  if (measured > 0) return Math.round(measured);
  const index = instance.indexFromElement(element);
  const cached = instance.itemSizeCache.get(instance.options.getItemKey(index));
  return cached && cached > 0 ? cached : instance.options.estimateSize(index);
}
