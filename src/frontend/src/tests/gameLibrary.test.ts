import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { nextTick, reactive } from "vue";
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
vi.mock("@tanstack/vue-virtual", async () => {
  const { ref } = await import("vue");
  return { useWindowVirtualizer: () => ref({}) };
});

function game(
  id: string,
  status: Game["status"],
  tags: string[],
  favorite = false,
): Game {
  return {
    id,
    title: id,
    status,
    tags,
    favorite,
    platforms: [],
    collections: [],
  } as unknown as Game;
}

beforeEach(() => {
  const data = new Map<string, string>();
  vi.stubGlobal("localStorage", {
    getItem: (key: string) => data.get(key) ?? null,
    setItem: (key: string, value: string) => data.set(key, value),
  });
  vi.stubGlobal("window", { innerWidth: 1440 });
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
  vi.clearAllMocks();
  vi.unstubAllGlobals();
});

async function library() {
  const { useGameLibrary } = await import("../composables/useGameLibrary");
  const result = useGameLibrary();
  result.games.value = [
    game("Alpha", "playing", ["Genre: Indie"], true),
    game("Beta", "backlog", ["Indie"]),
    game("Gamma", "playing", ["RPG"]),
  ];
  await nextTick();
  return result;
}

describe("game library filtering", () => {
  it("keeps local filters on a fresh visit but honors shared links on re-entry", async () => {
    const view = await library();
    view.toggleTagFilter("Indie");
    await nextTick();
    routing.route.path = "/";
    routing.route.query = {};
    routing.route.fullPath = "/";
    await nextTick();
    routing.route.path = "/games";
    routing.route.fullPath = "/games";
    await nextTick();
    expect(view.tagsFilter.value).toEqual(["Indie"]);
    expect(routing.route.query).toMatchObject({ tag: "Indie" });
  });
  it("counts matches across statuses without making other tabs disappear", async () => {
    const view = await library();
    view.toggleTagFilter("Indie");
    expect(view.statusCounts.value).toMatchObject({
      all: 2,
      playing: 1,
      backlog: 1,
      beaten: 0,
    });
    view.statusFilter.value = "playing";
    expect(view.filteredGames.value.map((g) => g.id)).toEqual(["Alpha"]);
    expect(view.statusCounts.value.all).toBe(2);
    view.favoritesOnly.value = true;
    expect(view.statusCounts.value.all).toBe(1);
    view.searchQuery.value = "Missing";
    expect(view.statusCounts.value.all).toBe(0);
  });

  it("shares selected tags, clears them from the URL, and restores history", async () => {
    const view = await library();
    view.toggleTagFilter("Indie");
    view.toggleTagFilter("RPG");
    await nextTick();
    expect(routing.route.query).toMatchObject({ tag: ["Indie", "RPG"] });
    expect(view.activeFilterPills.value).toHaveLength(2);
    view.activeFilterPills.value[0].clear();
    await nextTick();
    expect(routing.route.query).toMatchObject({ tag: "RPG" });
    view.clearAllFilters();
    await nextTick();
    expect(routing.route.query).toEqual({ filters: "1" });
    routing.route.query = { tag: "Genre: Indie", status: "backlog" };
    routing.route.fullPath = "/games?tag=Genre%3A+Indie&status=backlog";
    await nextTick();
    expect(view.tagsFilter.value).toEqual(["Indie"]);
    expect(view.filteredGames.value.map((g) => g.id)).toEqual(["Beta"]);
    expect(view.activeFilterPills.value).toHaveLength(1);
  });

  it("shared links override saved filters and migrate old genre selections", async () => {
    localStorage.setItem(
      "gameLibraryFilters",
      JSON.stringify({
        favoritesOnly: true,
        genreFilter: "Genre: Indie",
        tagsFilter: ["Indie"],
      }),
    );
    const saved = await library();
    expect(saved.tagsFilter.value).toEqual(["Indie"]);
    routing.route.query = { tag: "RPG" };
    routing.route.fullPath = "/games?tag=RPG";
    const shared = await library();
    expect(shared.favoritesOnly.value).toBe(false);
    expect(shared.tagsFilter.value).toEqual(["RPG"]);
    expect(shared.filteredGames.value.map((g) => g.id)).toEqual(["Gamma"]);
  });
});
