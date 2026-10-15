import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { nextTick, reactive } from "vue";
import { parseQuery, stringifyQuery, type LocationQueryRaw } from "vue-router";
import type { Game } from "../types/game";
import { DEFAULT_GAME_FILTERS } from "../utils/gameLibraryQuery";

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
  it("combines exclusions with includes and keeps counts, chips and reset in sync", async () => {
    routing.route.query = {
      tag: "Indie",
      not_status: "playing",
      not_tag: "RPG",
    };
    routing.route.fullPath = "/games?tag=Indie&not_status=playing&not_tag=RPG";
    const view = await library();
    expect(view.filteredGames.value.map((item) => item.id)).toEqual(["Beta"]);
    expect(view.statusCounts.value).toMatchObject({
      all: 1,
      playing: 0,
      backlog: 1,
    });
    expect(view.activeFilterPills.value.map((pill) => pill.label)).toContain(
      "Not playing",
    );
    view.activeFilterPills.value
      .find((pill) => pill.label === "Not playing")!
      .clear();
    expect(view.filteredGames.value.map((item) => item.id)).toEqual([
      "Alpha",
      "Beta",
    ]);
    view.clearAllFilters();
    await nextTick();
    expect(view.excludedStatuses.value).toEqual([]);
    expect(view.excludedTags.value).toEqual([]);
    expect(view.excludedPlatforms.value).toEqual([]);
    expect(view.statusCounts.value.all).toBe(3);
    expect(routing.route.query).toEqual({ filters: "1" });
  });

  it("excludes normalized platform families from any owned copy", async () => {
    routing.route.query = { not_platform: "ps5" };
    routing.route.fullPath = "/games?not_platform=ps5";
    const view = await library();
    const main = view.games.value[0]!;
    const copy = game("Console copy", "backlog", []);
    copy.parentGameId = main.id;
    copy.relationshipType = "owned_copy";
    copy.platforms = [
      {
        platform: "PlayStation 4",
        playtimeMinutes: 0,
        completionPercent: null,
        lastPlayedAt: null,
      },
    ];
    view.games.value = [...view.games.value, copy];
    expect(view.excludedPlatforms.value).toEqual(["PlayStation"]);
    expect(view.filteredGames.value.map((item) => item.id)).toEqual([
      "Beta",
      "Gamma",
    ]);
  });

  it("persists exclusions and clears them when applying a legacy preset", async () => {
    localStorage.setItem(
      "gameLibraryFilters",
      JSON.stringify({ excludedTags: ["Genre: Indie"] }),
    );
    const view = await library();
    expect(view.filteredGames.value.map((item) => item.id)).toEqual(["Gamma"]);
    view.toggleExclusion("status", "playing");
    await nextTick();
    expect(
      JSON.parse(localStorage.getItem("gameLibraryFilters")!).excludedStatuses,
    ).toEqual(["playing"]);
    view.applyPreset({
      name: "Old",
      filters: {
        ...DEFAULT_GAME_FILTERS,
        tagsFilter: ["Indie"],
        excludedStatuses: undefined,
        excludedTags: undefined,
        excludedPlatforms: undefined,
      },
    });
    expect(view.excludedStatuses.value).toEqual([]);
    expect(view.excludedTags.value).toEqual([]);
    expect(view.excludedPlatforms.value).toEqual([]);
  });

  it("reconciles a saved card status across filters, counts and the current preview", async () => {
    const view = await library();
    const selected = view.games.value[0]!;
    view.statusFilter.value = "playing";
    view.selectedGame.value = selected;
    view.onGameUpdated({ ...selected, status: "beaten" });
    expect(view.filteredGames.value.map((item) => item.id)).toEqual(["Gamma"]);
    expect(view.statusCounts.value.playing).toBe(1);
    expect(view.statusCounts.value.beaten).toBe(1);
    expect(view.selectedGame.value?.status).toBe("beaten");
  });
  it("removes an unfavorited card from the shared favorite filter without a reload", async () => {
    const view = await library();
    view.favoritesOnly.value = true;
    const selected = view.games.value[0]!;
    view.onGameUpdated({ ...selected, favorite: false });
    expect(view.filteredGames.value).toEqual([]);
    expect(view.statusCounts.value.all).toBe(0);
  });
  it("closes the filter panel in the URL while retaining active tags and unrelated parameters", async () => {
    routing.route.query = { filters: "1", tag: "Indie", campaign: "friends" };
    routing.route.fullPath = "/games?filters=1&tag=Indie&campaign=friends";
    const view = await library();
    expect(view.showAdvancedFilters.value).toBe(true);
    view.showAdvancedFilters.value = false;
    await nextTick();
    expect(routing.route.query).toMatchObject({
      filters: "0",
      tag: "Indie",
      campaign: "friends",
    });
    expect(view.tagsFilter.value).toEqual(["Indie"]);
    expect(view.filteredGames.value.map((g) => g.id)).toEqual([
      "Alpha",
      "Beta",
    ]);
    view.showAdvancedFilters.value = true;
    await nextTick();
    expect(routing.route.query).toMatchObject({ filters: "1", tag: "Indie" });
  });

  it("honors a closed shared filter link on load and browser history", async () => {
    localStorage.setItem(
      "gameLibraryFilters",
      JSON.stringify({ showAdvancedFilters: true, tagsFilter: ["RPG"] }),
    );
    routing.route.query = { filters: "0", tag: "Indie" };
    routing.route.fullPath = "/games?filters=0&tag=Indie";
    const view = await library();
    expect(view.showAdvancedFilters.value).toBe(false);
    expect(view.tagsFilter.value).toEqual(["Indie"]);
    routing.route.query = { filters: "1", tag: "RPG" };
    routing.route.fullPath = "/games?filters=1&tag=RPG";
    await nextTick();
    expect(view.showAdvancedFilters.value).toBe(true);
    routing.route.query = { filters: "0", tag: "Indie" };
    routing.route.fullPath = "/games?filters=0&tag=Indie";
    await nextTick();
    expect(view.showAdvancedFilters.value).toBe(false);
    expect(view.tagsFilter.value).toEqual(["Indie"]);
  });

  it("finds one main game through any owned copy while using main status and favorites", async () => {
    const view = await library();
    const main = game("Main", "playing", ["RPG"], true);
    const copy = game("Epic custom title", "backlog", []);
    copy.parentGameId = main.id;
    copy.relationshipType = "owned_copy";
    copy.source = "Epic Games";
    copy.platforms = [
      {
        platform: "PlayStation 5",
        playtimeMinutes: 10,
        completionPercent: null,
        lastPlayedAt: null,
      },
    ];
    copy.achievementTotal = 5;
    view.games.value = [main, copy];
    expect(view.filteredGames.value.map((entry) => entry.id)).toEqual(["Main"]);
    expect(view.ownedCopyCount(main)).toBe(2);
    view.metadataProviderFilter.value = "Epic Games";
    view.statusFilter.value = "playing";
    view.favoritesOnly.value = true;
    view.searchQuery.value = "Epic custom";
    expect(view.filteredGames.value.map((entry) => entry.id)).toEqual(["Main"]);
    expect(view.statusCounts.value.all).toBe(1);
    view.statusFilter.value = "backlog";
    expect(view.filteredGames.value).toEqual([]);
  });
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
    expect(routing.route.query).toEqual({ filters: "0" });
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
