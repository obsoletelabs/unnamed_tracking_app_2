import { afterEach, expect, it, vi } from "vitest";
import { fetchEpisodes as fetchAnimeEpisodes } from "../services/anime";
import { fetchEpisodes as fetchTvEpisodes } from "../services/tvShows";

function show(stillUrl: string | null) {
  return {
    id: "show",
    status: "WATCHING",
    created_at: 1,
    updated_at: 1,
    seasons: [
      {
        id: "season",
        season_number: 1,
        status: "WATCHING",
        created_at: 1,
        updated_at: 1,
        episodes: [
          {
            id: "ep 1",
            season_id: "season",
            episode_number: 1,
            still_url: stillUrl,
            created_at: 1,
            updated_at: 1,
          },
        ],
      },
    ],
  };
}

afterEach(() => vi.unstubAllGlobals());

it.each([
  ["tv", fetchTvEpisodes],
  ["anime", fetchAnimeEpisodes],
] as const)(
  "loads %s episode stills through this server, not the provider",
  async (kind, fetchEpisodes) => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(JSON.stringify(show("http://192.168.1.1/still.jpg")), {
            headers: { "Content-Type": "application/json" },
          }),
      ),
    );
    const loaded = await fetchEpisodes("show", "season");
    expect(loaded.seasons[0]!.episodes[0]!.stillUrl).toBe(
      `/api/media-image/${kind}/episode/ep%201/still`,
    );
  },
);
