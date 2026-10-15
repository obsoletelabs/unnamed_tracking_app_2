// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { defineComponent } from "vue";
import { mount, type VueWrapper } from "@vue/test-utils";
import { useMediaLibraryPage } from "../composables/useMediaLibraryPage";
import type { MediaPage } from "../services/mediaApi";
import type { LibraryFilters } from "../utils/libraryFilters";

interface Item {
  id: string;
  revision?: number;
}
const wrappers: VueWrapper[] = [];
beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-10-15T00:00:00Z"));
});
afterEach(() => {
  wrappers.splice(0).forEach((wrapper) => wrapper.unmount());
  vi.useRealTimers();
});

function page(offset = 0, count = 100, total = 350): MediaPage<Item> {
  return {
    items: Array.from({ length: count }, (_, index) => ({
      id: String(offset + index),
    })),
    offset,
    limit: 100,
    total,
    statusCounts: { WATCHED: total },
    scoreRanks: {},
  };
}
function library(revision: () => number = () => 0) {
  const fetchPage =
    vi.fn<
      (
        offset: number,
        limit: number,
        filters: LibraryFilters,
      ) => Promise<MediaPage<Item>>
    >();
  let state!: ReturnType<typeof useMediaLibraryPage<Item>>;
  wrappers.push(
    mount(
      defineComponent({
        setup() {
          state = useMediaLibraryPage(fetchPage, "movie", "movies", revision);
          return () => null;
        },
      }),
    ),
  );
  return { state, fetchPage };
}

it("keeps two minutes of freshness and refreshes the loaded extent with the active sort", async () => {
  const { state, fetchPage } = library();
  fetchPage.mockResolvedValueOnce(page()).mockResolvedValueOnce(page(100));
  await state.load();
  await state.loadMore();
  expect(state.entries.value).toHaveLength(200);
  await state.refreshLibrary();
  expect(fetchPage).toHaveBeenCalledTimes(2);
  vi.setSystemTime(Date.now() + 120_001);
  fetchPage.mockResolvedValueOnce(page()).mockResolvedValueOnce(page(100));
  await state.refreshLibrary();
  expect(
    fetchPage.mock.calls.slice(2).map(([offset, limit]) => [offset, limit]),
  ).toEqual([
    [0, 100],
    [100, 100],
  ]);
  expect(state.entries.value).toHaveLength(200);
  fetchPage.mockResolvedValueOnce(page());
  await state.load({ ...state.currentFilters.value, sort: "progress" });
  expect(fetchPage).toHaveBeenLastCalledWith(
    0,
    100,
    expect.objectContaining({ sort: "progress" }),
  );
  expect(state.entries.value).toHaveLength(100);
});

it("invalidates freshness after a save and keeps local edits made during a refresh", async () => {
  let revision = 0;
  const { state, fetchPage } = library(() => revision);
  fetchPage.mockResolvedValueOnce(page());
  await state.load();
  revision++;
  let finish!: (value: MediaPage<Item>) => void;
  fetchPage.mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  const refresh = state.refreshLibrary();
  expect(fetchPage).toHaveBeenCalledTimes(2);
  state.entries.value[0] = { id: "0", revision: 2 };
  state.entries.value = state.entries.value.filter((item) => item.id !== "1");
  finish(page());
  await refresh;
  expect(state.entries.value[0]).toEqual({ id: "0", revision: 2 });
  expect(state.entries.value.some((item) => item.id === "1")).toBe(false);
});

it("does not append a stale load-more response to a new query or change its loading state", async () => {
  const { state, fetchPage } = library();
  fetchPage.mockResolvedValueOnce(page());
  await state.load();
  let finishOld!: (value: MediaPage<Item>) => void;
  fetchPage.mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        finishOld = resolve;
      }),
  );
  const older = state.loadMore();
  fetchPage.mockResolvedValueOnce({
    ...page(0, 1, 1),
    items: [{ id: "new-query" }],
  });
  await state.load({ ...state.currentFilters.value, search: "New" });
  finishOld(page(100));
  await older;
  expect(state.entries.value).toEqual([{ id: "new-query" }]);
  expect(state.total.value).toBe(1);
  expect(state.loading.value).toBe(false);
  expect(state.error.value).toBeNull();
});

it.each([{ items: [] }, { items: [{ id: "0" }] }])(
  "stops pagination on empty or non-growing replies",
  async ({ items }) => {
    const { state, fetchPage } = library();
    fetchPage.mockResolvedValueOnce(page());
    await state.load();
    fetchPage.mockResolvedValueOnce({ ...page(100), items });
    await state.loadMore();
    expect(state.hasMore.value).toBe(false);
    expect(state.total.value).toBe(350);
    await state.loadMore();
    expect(fetchPage).toHaveBeenCalledTimes(2);
  },
);

it("retains cached rows when a loaded-extent refresh fails partway through", async () => {
  const { state, fetchPage } = library();
  fetchPage.mockResolvedValueOnce(page()).mockResolvedValueOnce(page(100));
  await state.load();
  await state.loadMore();
  const original = [...state.entries.value];
  vi.setSystemTime(Date.now() + 120_001);
  fetchPage
    .mockResolvedValueOnce({
      ...page(),
      items: page().items.map((item) => ({ ...item, revision: 2 })),
    })
    .mockRejectedValueOnce(new Error("Offline"));
  await state.refreshLibrary();
  expect(state.entries.value).toEqual(original);
  expect(state.error.value).toBe("Offline");
  expect(state.loading.value).toBe(false);
});
