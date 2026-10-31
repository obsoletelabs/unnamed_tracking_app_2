import { afterEach, expect, it, vi } from "vitest";
import { patchMovie } from "../services/movies";
import { patchAnime } from "../services/anime";
import { patchTVShow } from "../services/tvShows";

afterEach(() => vi.unstubAllGlobals());

it.each([
  ["movie", patchMovie],
  ["anime", patchAnime],
  ["tv", patchTVShow],
] as const)(
  "sends only supplied %s tracking fields, including explicit clears",
  async (kind, patch) => {
    const fetch = vi.fn(
      async (_url: string, _options: RequestInit) =>
        new Response(
          JSON.stringify({
            id: "a",
            status: "IN_PROGRESS",
            seasons: [],
            created_at: 1,
            updated_at: 1,
            tmdb_score: null,
            anilist_score: null,
            mal_score: null,
          }),
          { status: 200 },
        ),
    );
    vi.stubGlobal("fetch", fetch);
    await patch("a", {
      favorite: true,
      note: null,
      status: "in progress",
      ratingOverall: 7,
    });
    expect(fetch).toHaveBeenCalledWith(
      "/api/" + kind + "/update/a",
      expect.objectContaining({
        method: "PATCH",
        credentials: "include",
      }),
    );
    expect(JSON.parse(fetch.mock.calls[0]![1]!.body as string)).toEqual({
      favorite: true,
      note: null,
      status: "IN_PROGRESS",
      rating_overall: 7,
    });
    await patch("a", { note: undefined });
    expect(JSON.parse(fetch.mock.calls[1]![1]!.body as string)).toEqual({});
  },
);
