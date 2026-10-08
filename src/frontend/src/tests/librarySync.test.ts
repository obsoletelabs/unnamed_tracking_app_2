import { afterEach, describe, expect, it, vi } from "vitest";
import { syncLibrary } from "../services/librarySync";

afterEach(() => vi.unstubAllGlobals());

describe("Steam import steps", () => {
  it("enriches in small batches and continues after a failed batch", async () => {
    const ids = Array.from({ length: 12 }, (_, i) => String(i));
    const bodies: { game_ids: string[] }[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, options: RequestInit) => {
        if (url.endsWith("/steam"))
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
      if (url.endsWith("/steam"))
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
