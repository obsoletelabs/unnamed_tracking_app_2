import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { effectScope, nextTick, reactive, type EffectScope } from "vue";
import { parseQuery, stringifyQuery, type LocationQueryRaw } from "vue-router";
import type { Game } from "../types/game";

const routing = vi.hoisted(() => ({
  route: {} as Record<string, unknown>,
  replace: vi.fn(),
  push: vi.fn(),
}));
vi.mock("vue-router", async (original) => ({
  ...(await original<typeof import("vue-router")>()),
  useRoute: () => routing.route,
  useRouter: () => ({ replace: routing.replace, push: routing.push }),
}));
vi.mock("vue", async (original) => ({
  ...(await original<typeof import("vue")>()),
  onMounted: vi.fn(),
  onUnmounted: vi.fn(),
  onActivated: vi.fn(),
  onDeactivated: vi.fn(),
}));
vi.mock("../utils/useKeptAlive", () => ({ useKeptAlive: vi.fn() }));
const services = vi.hoisted(() => ({ fetchPage: vi.fn() }));
vi.mock("../services/games", async (original) => ({
  ...(await original<typeof import("../services/games")>()),
  fetchGamesForViewport: services.fetchPage,
  peekAllGames: vi.fn(() => null),
  fetchAchievementsSummary: vi.fn(async () => null),
}));
vi.mock("../services/gameDuplicates", () => ({
  fetchGameDuplicates: vi.fn(async () => ({
    pairs: [
      { first: { id: "bedrock" }, second: { id: "hades" } },
      { first: { id: "java" }, second: { id: "bedrock" } },
    ],
  })),
}));
vi.mock("../services/gameIdentity", async (original) => ({
  ...(await original<typeof import("../services/gameIdentity")>()),
  fetchLibraryLayout: vi.fn(async () => ({
    mode: "grouped",
    rules: {},
    groups: [
      {
        umbrellaId: "java",
        playtimeSeconds: null,
        members: [
          { gameId: "bedrock", display: "grouped", relationshipId: "e" },
        ],
      },
    ],
    related: [],
  })),
}));
vi.mock("@tanstack/vue-virtual", async () => {
  const { ref } = await import("vue");
  return { useWindowVirtualizer: () => ref({}) };
});

function game(id: string, title: string, platform = "PC"): Game {
  return {
    id,
    title,
    status: "backlog",
    tags: [],
    favorite: false,
    platforms: [{ platform, playtimeMinutes: 0 }],
    collections: [],
    parentGameId: null,
    relationshipType: null,
    source: null,
    ratingOverall: null,
    ratingStory: null,
    ratingGameplay: null,
    ratingSound: null,
  } as unknown as Game;
}

const scopes: EffectScope[] = [];
beforeEach(() => {
  services.fetchPage.mockReset();
  const data = new Map<string, string>();
  vi.stubGlobal("localStorage", {
    getItem: (key: string) => data.get(key) ?? null,
    setItem: (key: string, value: string) => data.set(key, value),
  });
  vi.stubGlobal("window", {
    innerWidth: 1440,
    setTimeout: (callback: () => void, delay: number) => {
      if (delay === 0) {
        callback();
        return 1;
      }
      return setTimeout(callback, delay);
    },
  });
  vi.stubGlobal("document", { documentElement: { clientWidth: 1440 } });
  routing.route = reactive({
    path: "/games",
    fullPath: "/games",
    hash: "",
    query: {},
  });
  routing.replace.mockImplementation(
    async ({ query }: { query: LocationQueryRaw }) => {
      routing.route.query = parseQuery(stringifyQuery(query));
      routing.route.fullPath = `/games?${stringifyQuery(query)}`;
    },
  );
});
afterEach(() => {
  routing.route.path = "/elsewhere";
  scopes.splice(0).forEach((scope) => scope.stop());
  vi.clearAllMocks();
  vi.unstubAllGlobals();
});

const page = (games: Game[], total = 4) => ({
  games,
  total,
  statusCounts: null,
});
async function library() {
  const { useGameLibrary } = await import("../composables/useGameLibrary");
  const scope = effectScope();
  scopes.push(scope);
  return scope.run(() => useGameLibrary())!;
}
async function settle() {
  for (let i = 0; i < 15; i++) {
    await Promise.resolve();
    await nextTick();
  }
}
it("drains all pages for a client filter even when consecutive pages have no matches", async () => {
  services.fetchPage
    .mockResolvedValueOnce(page([game("a", "Alpha")]))
    .mockResolvedValueOnce(page([game("b", "Beta")]))
    .mockResolvedValueOnce(page([game("c", "Gamma")]))
    .mockResolvedValueOnce(page([{ ...game("d", "Delta"), tags: ["RPG"] }]));
  const view = await library();
  view.tagsFilter.value = ["RPG"];
  await view.loadGames();
  await settle();
  expect(view.filteredGames.value.map((g) => g.id)).toEqual(["d"]);
  expect(services.fetchPage.mock.calls.map((call) => call[0])).toEqual([
    0, 1, 2, 3,
  ]);
});
it("finds the highest rating on the last page without scrolling", async () => {
  services.fetchPage
    .mockResolvedValueOnce(
      page([{ ...game("a", "Alpha"), ratingOverall: 2 }], 3),
    )
    .mockResolvedValueOnce(
      page([{ ...game("b", "Beta"), ratingOverall: 3 }], 3),
    )
    .mockResolvedValueOnce(
      page([{ ...game("c", "Gamma"), ratingOverall: 10 }], 3),
    );
  const view = await library();
  view.sortBy.value = "rating";
  await view.loadGames();
  await settle();
  expect(view.filteredGames.value.map((g) => g.id)).toEqual(["c", "b", "a"]);
});
it("keeps the ordinary library viewport paged", async () => {
  services.fetchPage.mockResolvedValueOnce(
    page(
      Array.from({ length: 30 }, (_, i) => game("g" + i, "Game " + i)),
      100,
    ),
  );
  const view = await library();
  await view.loadGames();
  await settle();
  expect(services.fetchPage).toHaveBeenCalledTimes(1);
  expect(view.hasMoreGames.value).toBe(true);
});
it("does not append an obsolete page to a replacement query", async () => {
  let finish!: (value: ReturnType<typeof page>) => void;
  services.fetchPage
    .mockResolvedValueOnce(page([game("a", "Alpha")]))
    .mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    )
    .mockResolvedValueOnce(page([game("new", "New")], 1));
  const view = await library();
  await view.loadGames();
  const old = view.loadMoreGames();
  await view.loadGames();
  finish(page([game("old", "Old")]));
  await old;
  await settle();
  expect(view.games.value.map((g) => g.id)).toEqual(["new"]);
  expect(view.totalGameCount.value).toBe(1);
});
it("does not let an old page clear a newer page's loading state", async () => {
  let finishOld!: (value: ReturnType<typeof page>) => void;
  let finishNew!: (value: ReturnType<typeof page>) => void;
  services.fetchPage
    .mockResolvedValueOnce(page([game("a", "Alpha")]))
    .mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finishOld = resolve;
        }),
    )
    .mockResolvedValueOnce(page([game("new", "New")], 2))
    .mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finishNew = resolve;
        }),
    );
  const view = await library();
  await view.loadGames();
  const old = view.loadMoreGames();
  await view.loadGames();
  const current = view.loadMoreGames();
  finishOld(page([game("old", "Old")]));
  await old;
  expect(view.loadingMoreGames.value).toBe(true);
  finishNew(page([game("new2", "New 2")], 2));
  await current;
  expect(view.games.value.map((g) => g.id)).toEqual(["new", "new2"]);
});
it.each([{ rows: [] }, { rows: [game("a", "Alpha")] }])(
  "stops empty or non-growing pages instead of spinning",
  async ({ rows }) => {
    services.fetchPage
      .mockResolvedValueOnce(page([game("a", "Alpha")]))
      .mockResolvedValueOnce(page(rows));
    const view = await library();
    await view.loadGames();
    await view.loadAllGames();
    await settle();
    expect(services.fetchPage).toHaveBeenCalledTimes(2);
    expect(view.hasMoreGames.value).toBe(false);
  },
);
it("retains loaded rows and retries a failed page at the same offset", async () => {
  services.fetchPage
    .mockResolvedValueOnce(page([game("a", "Alpha")], 2))
    .mockRejectedValueOnce(new Error("Offline"))
    .mockResolvedValueOnce(page([game("b", "Beta")], 2));
  const view = await library();
  await view.loadGames();
  await view.loadMoreGames();
  expect(view.games.value.map((g) => g.id)).toEqual(["a"]);
  await view.loadMoreGames();
  expect(services.fetchPage.mock.calls.map((call) => call[0])).toEqual([
    0, 1, 1,
  ]);
  expect(view.games.value.map((g) => g.id)).toEqual(["a", "b"]);
});

it("preserves the API's saved sorting-title order", async () => {
  services.fetchPage.mockResolvedValueOnce(
    page([game("z", "Zulu"), game("a", "Alpha")], 2),
  );
  const view = await library();
  await view.loadGames();
  expect(view.filteredGames.value.map((g) => g.id)).toEqual(["z", "a"]);
});
it("shows incomplete results without an empty claim while a filtered page is pending", async () => {
  let finish!: (value: ReturnType<typeof page>) => void;
  services.fetchPage
    .mockResolvedValueOnce(page([game("a", "Alpha")], 2))
    .mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
  const view = await library();
  view.tagsFilter.value = ["RPG"];
  await view.loadGames();
  await settle();
  expect(view.resultsIncomplete.value).toBe(true);
  expect(view.isEmpty.value).toBe(false);
  finish(page([{ ...game("b", "Beta"), tags: ["RPG"] }], 2));
  await settle();
  expect(view.resultsIncomplete.value).toBe(false);
});
it("retries an empty response at the same offset without claiming complete filtered counts", async () => {
  services.fetchPage
    .mockResolvedValueOnce(page([game("a", "Alpha")], 2))
    .mockResolvedValueOnce(page([], 2))
    .mockResolvedValueOnce(page([{ ...game("b", "Beta"), tags: ["RPG"] }], 2));
  const view = await library();
  view.tagsFilter.value = ["RPG"];
  await view.loadGames();
  await settle();
  expect(view.loadMoreError.value).not.toBeNull();
  expect(view.statusCountsComplete.value).toBe(false);
  await view.retryLoadMoreGames();
  await settle();
  expect(view.filteredGames.value.map((g) => g.id)).toEqual(["b"]);
  expect(services.fetchPage.mock.calls.map((call) => call[0])).toEqual([
    0, 1, 1,
  ]);
  expect(view.loadMoreError.value).toBeNull();
});

it("invalidates a page immediately while a search reload is debouncing", async () => {
  let finish!: (value: ReturnType<typeof page>) => void;
  services.fetchPage
    .mockResolvedValueOnce(page([game("a", "Alpha")], 4))
    .mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    )
    .mockResolvedValueOnce(page([game("b", "Beta")], 1));
  const view = await library();
  await view.loadGames();
  const old = view.loadMoreGames();
  view.searchQuery.value = "be";
  await nextTick();
  finish(page([game("old", "Old")], 4));
  await old;
  expect(view.games.value.map((g) => g.id)).toEqual(["a"]);
  await new Promise((resolve) => setTimeout(resolve, 220));
  await settle();
  expect(view.games.value.map((g) => g.id)).toEqual(["b"]);
  expect(services.fetchPage.mock.calls.map((call) => call[0])).toEqual([
    0, 1, 0,
  ]);
});
it("pauses a filtered scan after failure until the user retries", async () => {
  services.fetchPage
    .mockResolvedValueOnce(page([game("a", "Alpha")], 2))
    .mockRejectedValueOnce(new Error("Offline"))
    .mockResolvedValueOnce(page([{ ...game("b", "Beta"), tags: ["RPG"] }], 2));
  const view = await library();
  view.tagsFilter.value = ["RPG"];
  await view.loadGames();
  await settle();
  expect(services.fetchPage).toHaveBeenCalledTimes(2);
  expect(view.isEmpty.value).toBe(false);
  expect(view.loadMoreError.value).not.toBeNull();
  await view.retryLoadMoreGames();
  await settle();
  expect(view.filteredGames.value.map((g) => g.id)).toEqual(["b"]);
});
