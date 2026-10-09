import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createRenderer, nextTick, ssrContextKey } from "vue";
import MovieLibrary from "../views/MovieLibrary.vue";
import TVShowLibrary from "../views/TVShowLibrary.vue";
import AnimeLibrary from "../views/AnimeLibrary.vue";
import { fetchMoviesPage } from "../services/movies";
import { fetchTVShowsPage } from "../services/tvShows";
import { fetchAnimePage } from "../services/anime";
import type { LibraryFilters } from "../utils/libraryFilters";

vi.mock("../components/library/MediaLibraryView.vue", () => ({
  default: { render: () => null },
}));
vi.mock("../services/movies", () => ({ fetchMoviesPage: vi.fn() }));
vi.mock("../services/tvShows", () => ({ fetchTVShowsPage: vi.fn() }));
vi.mock("../services/anime", () => ({ fetchAnimePage: vi.fn() }));

const renderer = createRenderer<object, object>({
  createElement: () => ({}),
  createText: () => ({}),
  createComment: () => ({}),
  insert: () => {},
  remove: () => {},
  setText: () => {},
  setElementText: () => {},
  patchProp: () => {},
  parentNode: () => null,
  nextSibling: () => null,
});
const page = { items: [], total: 0, statusCounts: {}, scoreRanks: {} };
type EmptyPage = typeof page & { offset: number; limit: number };
interface LibraryState {
  error: string | null;
  loading: boolean;
  load(filters?: LibraryFilters & { statusBucket: string }): Promise<void>;
  loadMore(): Promise<void>;
  currentFilters: LibraryFilters & { statusBucket: string };
}
const apps: ReturnType<typeof renderer.createApp>[] = [];
beforeEach(() => {
  vi.stubGlobal("window", new EventTarget());
  vi.stubGlobal(
    "document",
    Object.assign(new EventTarget(), { visibilityState: "visible" }),
  );
  vi.stubGlobal("navigator", { onLine: true });
});
afterEach(() => {
  apps.splice(0).forEach((app) => app.unmount());
  vi.resetAllMocks();
  vi.unstubAllGlobals();
});
async function flush() {
  await nextTick();
  await Promise.resolve();
  await nextTick();
}

describe.each([
  ["movies", MovieLibrary, fetchMoviesPage],
  ["TV", TVShowLibrary, fetchTVShowsPage],
  ["anime", AnimeLibrary, fetchAnimePage],
] as const)("%s library recovery", (_, component, fetchPage) => {
  async function mount() {
    const app = renderer.createApp({ ...component, render: () => null });
    app.provide(ssrContextKey, { modules: new Set() });
    apps.push(app);
    app.mount({});
    await flush();
    return (app._instance as unknown as { setupState: LibraryState })
      .setupState;
  }
  it("clears a failed load after a successful retry", async () => {
    vi.mocked(fetchPage).mockRejectedValueOnce(new Error("Connection lost"));
    const state = await mount();
    expect(state.error).toBe("Connection lost");
    vi.mocked(fetchPage).mockResolvedValueOnce({
      ...page,
      offset: 0,
      limit: 100,
    });
    await state.load();
    expect(state.error).toBeNull();
    expect(state.loading).toBe(false);
  });
  it("retries the active filter when the connection returns", async () => {
    vi.mocked(fetchPage).mockRejectedValueOnce(new Error("Connection lost"));
    const state = await mount();
    state.currentFilters.search = "Puzzle";
    vi.mocked(fetchPage).mockResolvedValueOnce({
      ...page,
      offset: 0,
      limit: 100,
    });
    window.dispatchEvent(new Event("online"));
    await flush();
    expect(fetchPage).toHaveBeenCalledTimes(2);
    expect(fetchPage).toHaveBeenLastCalledWith(
      0,
      100,
      expect.objectContaining({ search: "Puzzle" }),
    );
    expect(state.error).toBeNull();
  });
  it("ignores an older request's failure while a newer filter is loading", async () => {
    let failOlder!: (reason: Error) => void;
    vi.mocked(fetchPage).mockImplementationOnce(
      () =>
        new Promise<EmptyPage>((_, reject) => {
          failOlder = reject;
        }),
    );
    const state = await mount();
    let finishNewer!: (value: EmptyPage) => void;
    vi.mocked(fetchPage).mockImplementationOnce(
      () =>
        new Promise<EmptyPage>((resolve) => {
          finishNewer = resolve;
        }),
    );
    const newest = state.load({ ...state.currentFilters, search: "New" });
    failOlder(new Error("Older request failed"));
    await flush();
    expect(state.error).toBeNull();
    expect(state.loading).toBe(true);
    finishNewer({ ...page, offset: 0, limit: 100 });
    await newest;
    expect(state.error).toBeNull();
    expect(state.loading).toBe(false);
  });
  it("clears pagination errors after loading more successfully", async () => {
    vi.mocked(fetchPage).mockResolvedValueOnce({
      ...page,
      total: 200,
      offset: 0,
      limit: 100,
    });
    const state = await mount();
    vi.mocked(fetchPage).mockRejectedValueOnce(new Error("Connection lost"));
    await state.loadMore();
    expect(state.error).toBe("Connection lost");
    vi.mocked(fetchPage).mockResolvedValueOnce({
      ...page,
      total: 200,
      offset: 0,
      limit: 100,
    });
    await state.loadMore();
    expect(state.error).toBeNull();
  });
});
