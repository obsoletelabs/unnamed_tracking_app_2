// A failed favourite change must say so and must not be started twice.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { nextTick, reactive } from "vue";
import type { Game } from "../types/game";

const routing = vi.hoisted(() => ({
  route: {} as Record<string, unknown>,
}));
vi.mock("vue-router", async (original) => ({
  ...(await original<typeof import("vue-router")>()),
  useRoute: () => routing.route,
  useRouter: () => ({
    replace: vi.fn().mockResolvedValue(undefined),
    push: vi.fn(),
  }),
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

const setFavorite = vi.fn();
vi.mock("../services/games", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../services/games")>()),
  setFavorite: (...args: unknown[]) => setFavorite(...args),
  fetchGames: vi.fn(),
}));

function game(id: string, favorite = false): Game {
  return {
    id,
    title: id,
    status: "playing",
    tags: [],
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
});
afterEach(() => {
  vi.clearAllMocks();
  vi.unstubAllGlobals();
});

async function library(list: Game[]) {
  const { useGameLibrary } = await import("../composables/useGameLibrary");
  const result = useGameLibrary();
  result.games.value = list;
  await nextTick();
  return result;
}

describe("library favourite toggle", () => {
  it("puts the star back and names the failed game", async () => {
    setFavorite.mockRejectedValue(new Error("down"));
    const alpha = game("Alpha");
    const view = await library([alpha]);
    expect(view.favoriteError.value).toBeNull();

    await view.toggleFavorite(alpha);

    expect(alpha.favorite).toBe(false);
    expect(view.favoriteError.value).toContain("Alpha");
  });

  it("clears the message when a later change succeeds", async () => {
    setFavorite.mockRejectedValueOnce(new Error("down"));
    const alpha = game("Alpha");
    const view = await library([alpha]);
    await view.toggleFavorite(alpha);
    expect(view.favoriteError.value).not.toBeNull();

    setFavorite.mockResolvedValue({ ...alpha, favorite: true });
    await view.toggleFavorite(alpha);
    expect(view.favoriteError.value).toBeNull();
  });

  it("ignores a second click while the first is still saving", async () => {
    let finish: (value: Game) => void = () => {};
    setFavorite.mockImplementation(
      () => new Promise<Game>((resolve) => (finish = resolve)),
    );
    const alpha = game("Alpha");
    const view = await library([alpha]);

    const first = view.toggleFavorite(alpha);
    await view.toggleFavorite(alpha);
    expect(setFavorite).toHaveBeenCalledTimes(1);

    finish({ ...alpha, favorite: true });
    await first;
    expect(alpha.favorite).toBe(true);
  });
});

describe("game page favourite toggle", () => {
  it("restores the star, reports the failure and allows another try", async () => {
    const { useFavoriteToggle } =
      await import("../composables/useFavoriteToggle");
    const current = game("Alpha");
    const toggle = useFavoriteToggle(() => current);

    setFavorite.mockRejectedValueOnce(new Error("down"));
    await toggle.toggleFavorite();
    expect(current.favorite).toBe(false);
    expect(toggle.favoriteError.value).toMatch(/Could not update/);

    setFavorite.mockResolvedValue({ ...current, favorite: true });
    await toggle.toggleFavorite();
    expect(current.favorite).toBe(true);
    expect(toggle.favoriteError.value).toBeNull();
  });

  it("ignores a second click while saving", async () => {
    const { useFavoriteToggle } =
      await import("../composables/useFavoriteToggle");
    let finish: (value: Game) => void = () => {};
    setFavorite.mockImplementation(
      () => new Promise<Game>((resolve) => (finish = resolve)),
    );
    const current = game("Alpha");
    const toggle = useFavoriteToggle(() => current);

    const first = toggle.toggleFavorite();
    await toggle.toggleFavorite();
    expect(setFavorite).toHaveBeenCalledTimes(1);
    finish({ ...current, favorite: true });
    await first;
  });
});

describe("library refresh failure", () => {
  it("keeps the games already shown and reports the failure", async () => {
    const view = await library([game("Alpha"), game("Beta")]);
    const { fetchGames } = await import("../services/games");
    vi.mocked(fetchGames).mockRejectedValue(new Error("Failed to fetch"));

    await view.loadGames();

    expect(view.error.value).toBe("Failed to fetch");
    expect(view.games.value.map((item) => item.id)).toEqual(["Alpha", "Beta"]);
  });
});
