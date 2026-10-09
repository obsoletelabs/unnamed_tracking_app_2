import { afterEach, expect, it, vi } from "vitest";
import { addMediaCandidate, metadataEpisodeTotal } from "../services/mediaAdd";
import { createMovie } from "../services/movies";
import {
  createTVShow,
  updateSeason as updateTVSeason,
} from "../services/tvShows";
import {
  createAnime,
  updateSeason as updateAnimeSeason,
} from "../services/anime";
import type {
  MetadataCandidate,
  MetadataMediaType,
} from "../services/metadata";
import type { QuickAddForm } from "../types/mediaLibrary";
import type { Movie } from "../types/movie";
import type { TVShow } from "../types/tv_show";
import type { Anime } from "../types/anime";

vi.mock("../services/movies", () => ({ createMovie: vi.fn() }));
vi.mock("../services/tvShows", () => ({
  createTVShow: vi.fn(),
  updateSeason: vi.fn(),
}));
vi.mock("../services/anime", () => ({
  createAnime: vi.fn(),
  updateSeason: vi.fn(),
}));
afterEach(() => vi.resetAllMocks());
const form: QuickAddForm = {
  status: "in progress",
  watched: 0,
  seen: false,
  score: 8,
  startDate: "2026-01-02",
  endDate: null,
};
function candidate(type: MetadataMediaType): MetadataCandidate {
  return {
    id: "selected",
    rank: 0,
    external_id: "provider-identity",
    title: "Same title",
    year: 2024,
    media_type: type,
    provider: "test",
    provider_name: "Test",
    providers: [],
    provider_ids: {
      imdb: "selected-imdb",
      tvmaze: "42",
      anilist: "73",
      mal: "19",
    },
    alternate_titles: [],
    metadata: {
      description: "Selected version",
      genres: ["Adventure"],
      release_date: "2024-01-02",
      runtime_minutes: 120,
      episode_count: 11,
      seasons: [
        { season_number: 1, title: "First", episode_count: 5, air_date: null },
        { season_number: 2, title: "Second", episode_count: 6, air_date: null },
      ],
    },
    assets: [
      {
        kind: "poster",
        url: "https://example.test/selected.jpg",
        width: null,
        height: null,
      },
    ],
  };
}

it("adds the selected movie identity and metadata without repeating a title search", async () => {
  vi.mocked(createMovie).mockResolvedValue({ id: "movie-id" } as Movie);
  const selected = candidate("movie");
  expect(await addMediaCandidate(selected, form)).toEqual({
    path: "/movies/movie-id",
    progressWarning: false,
  });
  expect(createMovie).toHaveBeenCalledWith(
    expect.objectContaining({
      title: "Same title",
      providerIds: selected.provider_ids,
      description: "Selected version",
      runtimeMinutes: 120,
      posterUrl: "https://example.test/selected.jpg",
      ratingOverall: 8,
      startDate: "2026-01-02",
    }),
  );
  expect(createTVShow).not.toHaveBeenCalled();
});

it("keeps TV episode identities and allocates progress across seasons", async () => {
  vi.mocked(createTVShow).mockResolvedValue({
    id: "tv-id",
    seasons: [
      { id: "s2", seasonNumber: 2, episodeCount: 6 },
      { id: "s1", seasonNumber: 1, episodeCount: 5 },
    ],
  } as TVShow);
  expect(
    await addMediaCandidate(candidate("tv_show"), { ...form, watched: 8 }),
  ).toEqual({ path: "/tv/tv-id", progressWarning: false });
  expect(createTVShow).toHaveBeenCalledWith(
    expect.objectContaining({
      externalId: "42",
      providerIds: candidate("tv_show").provider_ids,
      seasons: expect.arrayContaining([
        expect.objectContaining({ seasonNumber: 2, episodeCount: 6 }),
      ]),
    }),
  );
  expect(
    vi.mocked(updateTVSeason).mock.calls.map((call) => [call[1], call[2]]),
  ).toEqual([
    ["s1", { episodesWatched: 5 }],
    ["s2", { episodesWatched: 3 }],
  ]);
});

it("keeps AniList and MAL identities even when another provider owns the merged anime result", async () => {
  vi.mocked(createAnime).mockResolvedValue({
    id: "anime-id",
    seasons: [{ id: "s1", seasonNumber: 1, episodeCount: 11 }],
  } as Anime);
  await addMediaCandidate(candidate("anime"), { ...form, watched: 3 });
  expect(createAnime).toHaveBeenCalledWith(
    expect.objectContaining({
      anilistId: "73",
      externalId: "19",
      seasons: [{ seasonNumber: 1, episodeCount: 11 }],
    }),
  );
  expect(updateAnimeSeason).toHaveBeenCalledWith("anime-id", "s1", {
    episodesWatched: 3,
  });
});

it("returns the saved title with a progress warning after a season update fails", async () => {
  vi.mocked(createTVShow).mockResolvedValue({
    id: "saved",
    seasons: [{ id: "s1", seasonNumber: 1, episodeCount: 11 }],
  } as TVShow);
  vi.mocked(updateTVSeason).mockRejectedValue(new Error("Network offline"));
  expect(
    await addMediaCandidate(candidate("tv_show"), { ...form, watched: 3 }),
  ).toEqual({ path: "/tv/saved", progressWarning: true });
  expect(createTVShow).toHaveBeenCalledTimes(1);
});

it("does not report a guessed total when some season counts are unavailable", () => {
  const selected = candidate("tv_show");
  delete selected.metadata.episode_count;
  selected.metadata.seasons![1]!.episode_count = null;
  expect(metadataEpisodeTotal(selected)).toBeNull();
  expect(metadataEpisodeTotal(candidate("movie"))).toBeNull();
});
