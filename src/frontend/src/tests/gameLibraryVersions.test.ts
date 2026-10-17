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
const services = vi.hoisted(() => ({ games: [] as unknown[] }));
vi.mock("../services/games", async (original) => ({
  ...(await original<typeof import("../services/games")>()),
  fetchGames: vi.fn(async () => services.games),
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

async function library(mode: "grouped" | "separate" = "grouped") {
  const { useGameLibrary } = await import("../composables/useGameLibrary");
  const view = useGameLibrary();
  view.games.value = [
    game("java", "Minecraft Java Edition"),
    game("bedrock", "Minecraft Bedrock Edition", "Xbox"),
    game("dungeons", "Minecraft Dungeons"),
    game("legends", "Minecraft Legends"),
    game("hades", "Hades"),
  ];
  view.layout.value = {
    mode,
    rules: {},
    groups: [
      {
        umbrellaId: "java",
        members: [
          { gameId: "bedrock", display: "grouped", relationshipId: "e1" },
        ],
      },
    ],
    related: [],
  };
  await nextTick();
  return view;
}
const ids = (view: Awaited<ReturnType<typeof library>>) =>
  view.filteredGames.value.map((item) => item.id).sort();

describe("umbrella library", () => {
  it("lists a logical game once and keeps distinct games with a shared prefix", async () => {
    const view = await library();
    expect(ids(view)).toEqual(["dungeons", "hades", "java", "legends"]);
    expect(view.statusCounts.value.all).toBe(4);
    expect(view.versionsOf(view.games.value[0]!)).toHaveLength(2);
    view.searchQuery.value = "minecraft";
    expect(ids(view)).toEqual(["dungeons", "java", "legends"]);
    expect(view.searchContext(view.games.value[0]!)).toBeNull();
  });

  it("names the one matching version for a targeted query and opens it", async () => {
    const view = await library();
    view.searchQuery.value = "Minecraft Java";
    expect(ids(view)).toEqual(["java"]);
    view.searchQuery.value = "minecraft bedrock";
    expect(ids(view)).toEqual(["java"]);
    const context = view.searchContext(view.games.value[0]!);
    expect(context?.label).toBe("Bedrock Edition");
    expect(context?.game.id).toBe("bedrock");
    view.openGame(view.games.value[0]!);
    expect(routing.push).toHaveBeenCalledWith("/games/bedrock");
  });

  it("finds a version by its own title even though only the umbrella is listed", async () => {
    const view = await library();
    view.searchQuery.value = "bedrock edition";
    expect(ids(view)).toEqual(["java"]);
  });

  it("lists each version separately when the user chose that", async () => {
    const view = await library("separate");
    expect(ids(view)).toEqual([
      "bedrock",
      "dungeons",
      "hades",
      "java",
      "legends",
    ]);
  });

  it("keeps duplicate highlighting on the umbrella and ignores pairs already merged", async () => {
    const view = await library();
    services.games = view.games.value;
    await view.loadGames();
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect([...view.possibleDuplicateIds.value].sort()).toEqual([
      "hades",
      "java",
    ]);
  });

  it("filters by any version's platform", async () => {
    const view = await library();
    view.platformFilter.value = "Xbox";
    expect(ids(view)).toEqual(["java"]);
    view.platformFilter.value = "all";
    view.layout.value = null;
    expect(ids(view)).toContain("bedrock");
  });
});
