import { afterEach, describe, expect, it, vi } from "vitest";

import {
  fetchGame,
  fetchGames,
  peekAllGames,
  peekGame,
  setFavorite,
  setStatus,
} from "../services/games";
import type { BackendGame } from "../services/games";

function raw(id: string, title: string): BackendGame {
  return {
    id,
    title,
    sort_title: title.toLowerCase(),
    source: "Steam",
    platform: "PC",
    status: "BACKLOG",
    playtime_seconds: 0,
    created_at: 0,
    updated_at: 0,
    last_played_at: null,
    stale_since: null,
    completion_date: null,
    purchase_date: null,
    purchase_price: null,
    purchase_price_currency_code: null,
    physical_condition: null,
    rating_overall: null,
    rating_story: null,
    rating_gameplay: null,
    rating_soundtrack: null,
    time_to_beat_hours: null,
    tags: [],
    features: [],
    collections: [],
    links: [],
  } as unknown as BackendGame;
}

function serve(games: BackendGame[]) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => ({ ok: true, json: async () => games })),
  );
}

afterEach(() => vi.unstubAllGlobals());

describe("the game cache", () => {
  it("has no full list until one has been fetched, then has every game", async () => {
    expect(peekAllGames()).toBeNull();
    serve([raw("a", "Alpha"), raw("b", "Beta")]);
    await fetchGames();
    expect(peekAllGames()?.map((g) => g.title)).toEqual(["Alpha", "Beta"]);
    expect(peekGame("a")?.title).toBe("Alpha");
  });

  it("keeps the completion numbers a library filled in across a refresh", async () => {
    serve([raw("c", "Gamma")]);
    const [first] = await fetchGames();
    // the library fills these in from its achievements summary
    first.achievementTotal = 20;
    first.achievementPercent = 45;

    serve([raw("c", "Gamma")]);
    const [again] = await fetchGames();
    expect(again).not.toBe(first);
    expect(again.achievementTotal).toBe(20);
    expect(again.achievementPercent).toBe(45);
  });
  it.each(["favorite", "status"])(
    "remembers a successful %s mutation without losing completion data",
    async (kind) => {
      const original = raw(`mutation-${kind}`, "A game");
      serve([original]);
      const [before] = await fetchGames();
      before.achievementTotal = 2;
      before.achievementPercent = 50;
      before.achievements = [
        { id: "earned", name: "An achievement", unlockedAt: "2026-10-15" },
      ];
      const response = {
        ...original,
        favorite: kind === "favorite",
        status: kind === "status" ? "BEATEN" : "BACKLOG",
      };
      vi.stubGlobal(
        "fetch",
        vi.fn(async () => new Response(JSON.stringify(response))),
      );
      const saved =
        kind === "favorite"
          ? await setFavorite(original.id, true)
          : await setStatus(original.id, "beaten");
      expect(peekGame(original.id)).toBe(saved);
      expect(peekAllGames()?.find((game) => game.id === original.id)).toBe(
        saved,
      );
      expect(saved.achievementTotal).toBe(2);
      expect(saved.achievementPercent).toBe(50);
      expect(saved.achievements).toEqual(before.achievements);
    },
  );
  it.each(["list", "detail"])(
    "does not let an older %s response undo a successful card save",
    async (kind) => {
      const original = raw(`pending-${kind}`, "A game");
      serve([original]);
      await fetchGames();
      let finish!: (response: Response) => void;
      const fetch = vi
        .fn()
        .mockImplementationOnce(
          () =>
            new Promise<Response>((resolve) => {
              finish = resolve;
            }),
        )
        .mockResolvedValueOnce(
          new Response(JSON.stringify({ ...original, favorite: true })),
        );
      vi.stubGlobal("fetch", fetch);
      const pending = kind === "list" ? fetchGames() : fetchGame(original.id);
      await setFavorite(original.id, true);
      finish(
        new Response(JSON.stringify(kind === "list" ? [original] : original)),
      );
      const result = await pending;
      const game = Array.isArray(result) ? result[0] : result;
      expect(game?.favorite).toBe(true);
      expect(peekGame(original.id)?.favorite).toBe(true);
    },
  );
});
