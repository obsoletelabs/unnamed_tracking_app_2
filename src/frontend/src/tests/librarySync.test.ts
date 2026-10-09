import { afterEach, describe, expect, it, vi } from "vitest";
import { syncLibrary } from "../services/librarySync";

afterEach(() => vi.unstubAllGlobals());

describe("Steam import steps", () => {
  it("saves owned games first and fetches achievements in bounded batches", async () => {
    const ids = Array.from({ length: 12 }, (_, i) => String(i));
    const achievementBatches: {
      game_ids: string[];
      status_game_ids: string[];
    }[] = [];
    const fetch = vi.fn(async (url: string, options: RequestInit) => {
      if (url.includes("/steam?"))
        return {
          ok: true,
          json: async () => ({
            games_added: 12,
            games: [],
            achievements_synced: 0,
            achievement_game_ids: ids,
            status_game_ids: ["0", "6"],
          }),
        };
      if (url.endsWith("/achievements")) {
        const body = JSON.parse(String(options.body));
        achievementBatches.push(body);
        return {
          ok: true,
          json: async () => ({
            achievements_synced: body.game_ids.length,
            achievements_unavailable: [],
          }),
        };
      }
      return { ok: true, json: async () => ({ added: 0, game_ids: [] }) };
    });
    vi.stubGlobal("fetch", fetch);
    const result = await syncLibrary("steam");
    expect(fetch.mock.calls[0]?.[0]).toBe(
      "/api/library-sync/steam?achievements=later",
    );
    expect(achievementBatches.map((batch) => batch.game_ids.length)).toEqual([
      5, 5, 2,
    ]);
    expect(achievementBatches.map((batch) => batch.status_game_ids)).toEqual([
      ["0"],
      ["6"],
      [],
    ]);
    expect(result.achievements_synced).toBe(12);
  });

  it("continues after achievement failures and keeps saved game counts", async () => {
    let batches = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url.includes("/steam?"))
          return {
            ok: true,
            json: async () => ({
              games_added: 6,
              games_updated: 2,
              games: [],
              achievements_synced: 0,
              achievement_game_ids: ["0", "1", "2", "3", "4", "5"],
            }),
          };
        if (url.endsWith("/achievements")) {
          if (++batches === 1) return { ok: false, status: 502 };
          return {
            ok: true,
            json: async () => ({
              achievements_synced: 2,
              achievements_unavailable: ["Unavailable game"],
            }),
          };
        }
        return { ok: true, json: async () => ({ added: 0, game_ids: [] }) };
      }),
    );
    const result = await syncLibrary("steam");
    expect(batches).toBe(2);
    expect(result.games_added).toBe(6);
    expect(result.games_updated).toBe(2);
    expect(result.achievements_synced).toBe(2);
    expect(result.achievements_failed).toBe(5);
    expect(result.achievements_unavailable).toEqual(["Unavailable game"]);
  });

  it("enriches in small batches and continues after a failed batch", async () => {
    const ids = Array.from({ length: 12 }, (_, i) => String(i));
    const bodies: { game_ids: string[] }[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, options: RequestInit) => {
        if (url.includes("/steam?"))
          return {
            ok: true,
            json: async () => ({
              games_added: 12,
              games: [],
              enrich_game_ids: ids,
            }),
          };
        if (url.endsWith("/wishlist"))
          return { ok: true, json: async () => ({ added: 0, game_ids: [] }) };
        bodies.push(JSON.parse(String(options.body)));
        if (bodies.length === 2) return { ok: false, status: 502 };
        return { ok: true, json: async () => ({ failed: 0 }) };
      }),
    );
    const result = await syncLibrary("steam");
    expect(bodies.map((body) => body.game_ids.length)).toEqual([5, 5, 2]);
    expect(result.games_added).toBe(12);
    expect(result.enrich_failed).toBe(5);
  });

  it("still enriches owned games when the optional wishlist request fails", async () => {
    const fetch = vi.fn(async (url: string) => {
      if (url.includes("/steam?"))
        return {
          ok: true,
          json: async () => ({
            games_added: 1,
            games: [],
            enrich_game_ids: ["1"],
          }),
        };
      if (url.endsWith("/wishlist")) return { ok: false, status: 502 };
      return { ok: true, json: async () => ({ failed: 0 }) };
    });
    vi.stubGlobal("fetch", fetch);
    const result = await syncLibrary("steam");
    expect(result.wishlist_failed).toBe(true);
    expect(result.enrich_failed).toBe(0);
    expect(fetch.mock.calls.some(([url]) => url.endsWith("/enrich"))).toBe(
      true,
    );
  });
});
