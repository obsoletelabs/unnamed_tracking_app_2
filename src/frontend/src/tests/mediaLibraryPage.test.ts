// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { defineComponent } from "vue";
import { mount, type VueWrapper } from "@vue/test-utils";
import { useMediaLibraryPage } from "../composables/useMediaLibraryPage";
import { currentUser } from "../state/auth";
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

function page(offset = 0, count = 48, total = 350): MediaPage<Item> {
  return {
    items: Array.from({ length: count }, (_, index) => ({
      id: String(offset + index),
    })),
    offset,
    limit: 48,
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
  fetchPage
    .mockResolvedValueOnce(page())
    .mockResolvedValueOnce({ ...page(48), statusCounts: {}, scoreRanks: {} });
  await state.load();
  await state.loadMore();
  expect(state.entries.value).toHaveLength(96);
  expect(state.statusCounts.value).toEqual({ WATCHED: 350 });
  await state.refreshLibrary();
  expect(fetchPage).toHaveBeenCalledTimes(2);
  vi.setSystemTime(Date.now() + 120_001);
  fetchPage.mockResolvedValueOnce(page()).mockResolvedValueOnce(page(48));
  await state.refreshLibrary();
  expect(
    fetchPage.mock.calls.slice(2).map(([offset, limit]) => [offset, limit]),
  ).toEqual([
    [0, 48],
    [48, 48],
  ]);
  expect(state.entries.value).toHaveLength(96);
  fetchPage.mockResolvedValueOnce(page());
  await state.load({ ...state.currentFilters.value, sort: "progress" });
  expect(fetchPage).toHaveBeenLastCalledWith(
    0,
    48,
    expect.objectContaining({ sort: "progress" }),
  );
  expect(state.entries.value).toHaveLength(48);
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
  finishOld(page(48));
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
    fetchPage.mockResolvedValueOnce({ ...page(48), items });
    await state.loadMore();
    expect(state.hasMore.value).toBe(false);
    expect(state.total.value).toBe(350);
    await state.loadMore();
    expect(fetchPage).toHaveBeenCalledTimes(2);
  },
);

it("retains cached rows when a loaded-extent refresh fails partway through", async () => {
  const { state, fetchPage } = library();
  fetchPage.mockResolvedValueOnce(page()).mockResolvedValueOnce(page(48));
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

it("tries every selected removal, deduplicates IDs and refreshes counts after partial failure", async () => {
  const { state, fetchPage } = library();
  fetchPage.mockResolvedValueOnce(page(0, 3, 3));
  await state.load({ ...state.currentFilters.value, search: "Test" });
  const remove = vi
    .fn<(id: string) => Promise<void>>()
    .mockImplementation(async (id) => {
      if (id === "1") throw new Error("Cannot remove this title");
    });
  fetchPage.mockResolvedValueOnce({ ...page(0, 1, 1), items: [{ id: "1" }] });
  const result = await state.removeEntries(["0", "1", "0", "2"], remove);
  expect(remove.mock.calls.map(([id]) => id)).toEqual(["0", "1", "2"]);
  expect(result).toEqual({
    failedIds: ["1"],
    error: "Cannot remove this title",
  });
  expect(state.entries.value).toEqual([{ id: "1" }]);
  expect(state.total.value).toBe(1);
  expect(state.statusCounts.value).toEqual({ WATCHED: 1 });
  expect(fetchPage).toHaveBeenLastCalledWith(
    0,
    48,
    expect.objectContaining({ search: "Test" }),
  );
});

it("does not resurrect removed rows when the post-removal refresh fails", async () => {
  const { state, fetchPage } = library();
  fetchPage.mockResolvedValueOnce(page(0, 3, 3));
  await state.load();
  const remove = vi
    .fn<(id: string) => Promise<void>>()
    .mockImplementation(async (id) => {
      if (id === "1") throw new Error("Cannot remove this title");
    });
  fetchPage.mockRejectedValueOnce(new Error("Refresh offline"));
  expect(
    (await state.removeEntries(["0", "1", "2"], remove)).failedIds,
  ).toEqual(["1"]);
  expect(state.entries.value).toEqual([{ id: "1" }]);
  expect(state.error.value).toBe("Refresh offline");
});

it("refreshes even if every removal fails", async () => {
  const { state, fetchPage } = library();
  fetchPage.mockResolvedValue(page(0, 2, 2));
  await state.load();
  const remove = vi.fn().mockRejectedValue(new Error("Offline"));
  expect((await state.removeEntries(["0", "1"], remove)).failedIds).toEqual([
    "0",
    "1",
  ]);
  expect(remove).toHaveBeenCalledTimes(2);
  expect(fetchPage).toHaveBeenCalledTimes(2);
});

it("stops a pending removal batch when the signed-in account changes", async () => {
  const previous = currentUser.value;
  currentUser.value = {
    id: "A",
    username: "A",
    email: "a@example.test",
    is_admin: false,
    steamgriddb_api_key: null,
  };
  try {
    const { state, fetchPage } = library();
    fetchPage.mockResolvedValueOnce(page(0, 2, 2));
    await state.load();
    let finish!: () => void;
    const remove = vi.fn().mockImplementationOnce(
      () =>
        new Promise<void>((resolve) => {
          finish = resolve;
        }),
    );
    const pending = state.removeEntries(["0", "1"], remove);
    currentUser.value = { ...currentUser.value, id: "B" };
    finish();
    await pending;
    expect(remove).toHaveBeenCalledTimes(1);
    expect(fetchPage).toHaveBeenCalledTimes(1);
  } finally {
    currentUser.value = previous;
  }
});
