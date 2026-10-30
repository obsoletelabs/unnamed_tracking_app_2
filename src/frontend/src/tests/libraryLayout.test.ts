// @vitest-environment happy-dom
import { expect, it } from "vitest";
import { Virtualizer } from "@tanstack/vue-virtual";
import { measureLibraryRow } from "../utils/libraryLayout";

function grid() {
  return new Virtualizer<Window, Element>({
    count: 4,
    getScrollElement: () => null,
    estimateSize: () => 330,
    scrollToFn: () => {},
    observeElementRect: () => {},
    observeElementOffset: () => {},
    initialRect: { width: 1920, height: 1080 },
    measureElement: measureLibraryRow,
  });
}

function observerEntry(
  target: Element,
  blockSize: number,
): ResizeObserverEntry {
  return {
    target,
    borderBoxSize: [{ blockSize, inlineSize: 200 }],
    contentRect: target.getBoundingClientRect(),
    contentBoxSize: [],
    devicePixelContentBoxSize: [],
  };
}

it("keeps initial rows separated before DOM measurement is available", () => {
  const virtualizer = grid();
  virtualizer.getVirtualItems();
  for (let index = 0; index < 4; index++) {
    const row = document.createElement("div");
    row.dataset.index = String(index);
    expect(row.offsetHeight).toBe(0);
    virtualizer.measureElement(row);
  }
  expect(virtualizer.getVirtualItems().map((item) => item.start)).toEqual([
    0, 330, 660, 990,
  ]);
  expect(virtualizer.getTotalSize()).toBe(1320);
});

it("uses real observer sizes and retains them through hidden or synchronous reads", () => {
  const virtualizer = grid();
  virtualizer.getVirtualItems();
  const row = document.createElement("div");
  row.dataset.index = "0";
  const entry = observerEntry(row, 407.2);
  const size = measureLibraryRow(row, entry, virtualizer);
  expect(size).toBe(407);
  virtualizer.resizeItem(0, size);
  expect(measureLibraryRow(row, undefined, virtualizer)).toBe(407);
  expect(measureLibraryRow(row, observerEntry(row, 0), virtualizer)).toBe(407);
  expect(virtualizer.getVirtualItems()[1].start).toBe(407);
});
