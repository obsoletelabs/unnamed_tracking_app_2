import { afterEach, expect, it, vi } from "vitest";
import { fetchCollectionIndex } from "../services/collections";

afterEach(() => vi.unstubAllGlobals());

it("loads the compact collection projection without fetching full game pages", async () => {
  const fetcher = vi.fn(
    async () =>
      new Response(
        JSON.stringify([
          {
            id: "f5c283e5-01ec-4e77-b0a7-67d515d304e0",
            title: "Puzzle game",
            status: "ON_HOLD",
            favorite: true,
            source: "Steam",
            tags: ["Puzzle"],
            collections: ["Favorites shelf"],
            playtime_seconds: 7200,
          },
        ]),
      ),
  );
  vi.stubGlobal("fetch", fetcher);

  const games = await fetchCollectionIndex();

  expect(fetcher).toHaveBeenCalledWith("/api/game/collection-index", {
    credentials: "include",
  });
  expect(games).toHaveLength(1);
  expect(games[0]).toMatchObject({
    title: "Puzzle game",
    status: "on hold",
    favorite: true,
    collections: ["Favorites shelf"],
    platforms: [{ playtimeMinutes: 120 }],
  });
  expect(games[0].coverImageUrl).toContain("/api/game/");
});
