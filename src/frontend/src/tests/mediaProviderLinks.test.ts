import { describe, expect, it } from "vitest";
import { providerLinks } from "../utils/mediaProviderLinks";

describe("providerLinks", () => {
  it("links an anime to the sites that know it", () => {
    expect(
      providerLinks("anime", {
        anilist: "21",
        mal: "21",
        "tmdb.tv": "37854",
        imdb: "tt0388629",
      }).map((link) => link.url),
    ).toEqual([
      "https://anilist.co/anime/21",
      "https://myanimelist.net/anime/21",
      "https://www.themoviedb.org/tv/37854",
      "https://www.imdb.com/title/tt0388629/",
    ]);
  });

  it("picks the TMDB page by media type", () => {
    expect(providerLinks("movie", { tmdb: "603" })[0].url).toBe(
      "https://www.themoviedb.org/movie/603",
    );
    expect(providerLinks("tv", { tmdb: "1396", tvmaze: "169" })).toHaveLength(
      2,
    );
  });

  it("ignores identifiers that are not the expected shape", () => {
    expect(
      providerLinks("movie", {
        tmdb: "603/../../x",
        imdb: "javascript:alert(1)",
        anilist: "21",
      }),
    ).toEqual([]);
    expect(providerLinks("movie", undefined)).toEqual([]);
  });
});
