// @vitest-environment happy-dom
import { flushPromises, shallowMount } from "@vue/test-utils";
import { reactive } from "vue";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import MovieDetail from "../views/MovieDetail.vue";
import AnimeDetail from "../views/AnimeDetail.vue";
import TVShowDetail from "../views/TVShowDetail.vue";
import * as movies from "../services/movies";
import * as anime from "../services/anime";
import * as tv from "../services/tvShows";
import type { Movie } from "../types/movie";
import type {
  Anime,
  AnimeEpisode,
  AnimeSeason as Season,
} from "../types/anime";
import type { TVShow } from "../types/tv_show";

const route = reactive({ params: { id: "a" } as { id: string } });
vi.mock("vue-router", () => ({
  useRoute: () => route,
  useRouter: () => ({ push: vi.fn(), back: vi.fn() }),
}));
vi.mock("../services/movies");
vi.mock("../services/anime");
vi.mock("../services/tvShows");

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}
const base = {
  userId: "u",
  sortTitle: "",
  description: null,
  studios: [],
  countries: [],
  languages: [],
  genres: [],
  tags: [],
  features: [],
  ageRating: null,
  source: null,
  posterUrl: null,
  backdropUrl: null,
  status: "wishlist" as const,
  priority: null,
  favorite: false,
  rewatches: 0,
  note: null,
  startDate: null,
  endDate: null,
  ratingStory: null,
  ratingPerformance: null,
  ratingSoundtrack: null,
  ratingOverall: null,
  personalRank: null,
  lockedFields: [],
  createdAt: "2026-01-01",
  updatedAt: "2026-01-01",
};
const movie = (id: string, extra: Partial<Movie> = {}): Movie => ({
  ...base,
  id,
  title: `Title ${id}`,
  releaseDate: null,
  runtimeMinutes: null,
  director: null,
  writer: null,
  tmdbScore: null,
  progressMinutes: null,
  ...extra,
});
const episodes = (id: string): AnimeEpisode[] => [
  {
    id: `${id}-e1`,
    seasonId: `s-${id}`,
    episodeNumber: 1,
    title: null,
    description: null,
    airDate: null,
    runtimeMinutes: null,
    stillUrl: null,
    watched: false,
    rating: null,
    note: null,
    createdAt: "2026-01-01",
    updatedAt: "2026-01-01",
  },
];
const season = (id: string, withEpisodes: boolean): Season => ({
  id: `s-${id}`,
  showId: id,
  seasonNumber: 1,
  name: null,
  episodeCount: null,
  episodesWatched: 0,
  status: "wishlist",
  airDate: null,
  posterUrl: null,
  episodes: withEpisodes ? episodes(id) : [],
  createdAt: "2026-01-01",
  updatedAt: "2026-01-01",
});
const show = (id: string, withEpisodes = false): TVShow => ({
  ...base,
  id,
  title: `Title ${id}`,
  firstAirDate: null,
  episodeRuntimeMinutes: null,
  creators: [],
  tmdbScore: null,
  externalId: null,
  seasons: [season(id, withEpisodes)],
  isAiring: null,
  nextEpisodeAirAt: null,
  nextEpisodeNumber: null,
  airingIntervalDays: null,
});
const cartoon = (id: string, withEpisodes = false): Anime => ({
  ...show(id, withEpisodes),
  titleEnglish: null,
  titleRomaji: null,
  titleNative: null,
  episodeRuntimeMinutes: null,
  format: null,
  anilistScore: null,
  malScore: null,
  anilistId: null,
  kitsuId: null,
  linkedTvShowId: null,
  linkedMovieId: null,
  seasons: [season(id, withEpisodes)],
});

// Keep the real retry control, stub everything else.
const stubs = { RetryMessage: false, RouterLink: true };
const mountView = (view: object) => {
  const wrapper = shallowMount(view, { global: { stubs } });
  return wrapper;
};
const state = (wrapper: ReturnType<typeof mountView>) =>
  wrapper.vm as unknown as Record<string, { value?: unknown } | unknown>;

beforeEach(() => {
  vi.resetAllMocks();
  route.params.id = "a";
  vi.mocked(movies.peekMovie).mockReturnValue(undefined);
  vi.mocked(anime.peekAnime).mockReturnValue(undefined);
  vi.mocked(tv.peekTVShow).mockReturnValue(undefined);
  vi.mocked(anime.peekAllAnimes).mockReturnValue(null);
  vi.mocked(anime.fetchAnime).mockResolvedValue([]);
  vi.mocked(anime.peekAnimeRelations).mockReturnValue(undefined);
});
afterEach(() => vi.useRealTimers());

describe("movie detail load", () => {
  it("shows the newest title when an older request answers last", async () => {
    const slowA = deferred<Movie>();
    vi.mocked(movies.getMovie).mockImplementation((id: string) =>
      id === "a" ? slowA.promise : Promise.resolve(movie("b")),
    );
    const wrapper = mountView(MovieDetail);
    route.params.id = "b";
    await flushPromises();
    slowA.resolve(movie("a"));
    await flushPromises();
    expect((state(wrapper).movie as { id: string }).id).toBe("b");
    expect(state(wrapper).loading).toBe(false);
    wrapper.unmount();
  });

  it("clears an earlier failure when the next title loads", async () => {
    vi.mocked(movies.getMovie)
      .mockRejectedValueOnce(new Error("Movie A failed"))
      .mockResolvedValueOnce(movie("b"));
    const wrapper = mountView(MovieDetail);
    await flushPromises();
    expect(wrapper.text()).toContain("Movie A failed");
    route.params.id = "b";
    await flushPromises();
    expect(wrapper.text()).not.toContain("Movie A failed");
    expect((state(wrapper).movie as { id: string }).id).toBe("b");
    wrapper.unmount();
  });

  it("offers Try again on a failed load", async () => {
    vi.mocked(movies.getMovie)
      .mockRejectedValueOnce(new Error("Offline"))
      .mockResolvedValueOnce(movie("a"));
    const wrapper = mountView(MovieDetail);
    await flushPromises();
    await wrapper.get("button").trigger("click");
    await flushPromises();
    expect(wrapper.text()).not.toContain("Offline");
    expect((state(wrapper).movie as { id: string }).id).toBe("a");
    wrapper.unmount();
  });

  it("says a saved copy is shown when the refresh fails, with Try again", async () => {
    vi.mocked(movies.peekMovie).mockReturnValue(movie("a"));
    vi.mocked(movies.getMovie)
      .mockRejectedValueOnce(new Error("Offline"))
      .mockResolvedValueOnce(movie("a", { title: "Fresh" }));
    const wrapper = mountView(MovieDetail);
    await flushPromises();
    expect(wrapper.text()).toContain("Showing a saved copy");
    await wrapper.get(".retry-message button").trigger("click");
    await flushPromises();
    expect(wrapper.text()).not.toContain("Showing a saved copy");
    expect((state(wrapper).movie as { title: string }).title).toBe("Fresh");
    wrapper.unmount();
  });

  it("does not show the previous title's related list after navigating", async () => {
    const slow = deferred<movies.MovieRelationsResponse>();
    vi.mocked(movies.getMovie).mockImplementation(async (id: string) =>
      movie(id),
    );
    vi.mocked(movies.fetchMovieRelations).mockReturnValueOnce(slow.promise);
    const wrapper = mountView(MovieDetail);
    await flushPromises();
    (wrapper.vm as unknown as { setTab: (tab: string) => void }).setTab(
      "related",
    );
    route.params.id = "b";
    await flushPromises();
    slow.resolve({
      related: [{ id: 1, title: "A's sequel", year: null, posterUrl: null }],
      collectionName: "A",
      configured: true,
    });
    await flushPromises();
    expect(state(wrapper).relatedList).toEqual([]);
    expect(state(wrapper).relatedLoaded).toBe(false);
    wrapper.unmount();
  });
});

describe("anime and TV detail load", () => {
  it("anime: a slow answer for the previous title never replaces the new one", async () => {
    const slowA = deferred<Anime>();
    vi.mocked(anime.getAnime).mockImplementation((id: string) =>
      id === "a" ? slowA.promise : Promise.resolve(cartoon("b")),
    );
    const wrapper = mountView(AnimeDetail);
    route.params.id = "b";
    await flushPromises();
    slowA.resolve(cartoon("a"));
    await flushPromises();
    expect((state(wrapper).show as { id: string }).id).toBe("b");
    wrapper.unmount();
  });

  it("anime: Try again retries a failed episodes load", async () => {
    vi.mocked(anime.getAnime).mockResolvedValue(cartoon("a"));
    vi.mocked(anime.fetchEpisodes)
      .mockRejectedValueOnce(new Error("Episodes down"))
      .mockResolvedValueOnce(cartoon("a", true));
    const wrapper = mountView(AnimeDetail);
    await flushPromises();
    (wrapper.vm as unknown as { setTab: (tab: string) => void }).setTab(
      "episodes",
    );
    await flushPromises();
    expect(wrapper.text()).toContain("Episodes down");
    expect(wrapper.text()).not.toContain("No episode data yet");
    await wrapper.get(".tab-panel .retry-message button").trigger("click");
    await flushPromises();
    expect(wrapper.text()).not.toContain("Episodes down");
    expect(anime.fetchEpisodes).toHaveBeenCalledTimes(2);
    wrapper.unmount();
  });

  it("TV: leaving a title drops its slow episodes answer", async () => {
    const slow = deferred<TVShow>();
    vi.mocked(tv.getTVShow).mockImplementation(async (id: string) => show(id));
    vi.mocked(tv.fetchEpisodes).mockReturnValueOnce(slow.promise);
    const wrapper = mountView(TVShowDetail);
    await flushPromises();
    (wrapper.vm as unknown as { setTab: (tab: string) => void }).setTab(
      "episodes",
    );
    route.params.id = "b";
    await flushPromises();
    slow.resolve(show("a", true));
    await flushPromises();
    expect((state(wrapper).show as { id: string }).id).toBe("b");
    expect(state(wrapper).episodesLoading).toBe(false);
    wrapper.unmount();
  });
});

describe("related title preview", () => {
  it("shows the newest clicked title when an older preview answers last", async () => {
    vi.mocked(movies.getMovie).mockResolvedValue(movie("a"));
    vi.mocked(movies.fetchMovies).mockResolvedValue([]);
    const first =
      deferred<Awaited<ReturnType<typeof movies.searchMovieMetadata>>>();
    vi.mocked(movies.searchMovieMetadata)
      .mockReturnValueOnce(first.promise)
      .mockResolvedValueOnce({
        results: [{ title: "Second", description: "Second plot" }],
      } as never);
    const wrapper = mountView(MovieDetail);
    await flushPromises();
    const click = state(wrapper).onRelatedTitleClick as (r: {
      title: string;
      posterUrl: null;
    }) => Promise<void>;
    const a = click({ title: "First", posterUrl: null });
    await flushPromises();
    await click({ title: "Second", posterUrl: null });
    first.resolve({
      results: [{ title: "First", description: "First plot" }],
    } as never);
    await a;
    expect(state(wrapper).previewTitle).toBe("Second");
    expect(state(wrapper).previewDescription).toBe("Second plot");
    expect(state(wrapper).previewLoading).toBe(false);
    wrapper.unmount();
  });

  it("still previews a title when the library cannot be read", async () => {
    vi.mocked(anime.getAnime).mockResolvedValue(cartoon("a"));
    vi.mocked(anime.fetchAnime).mockRejectedValue(new Error("Down"));
    vi.mocked(anime.fetchAnimeMetadataByAnilistId).mockResolvedValue(null);
    vi.mocked(anime.searchAnimeMetadata).mockResolvedValue({
      results: [],
    } as never);
    const wrapper = mountView(AnimeDetail);
    await flushPromises();
    await (state(wrapper).onRelatedTitleClick as (r: object) => Promise<void>)({
      id: 7,
      title: "Sequel",
      posterUrl: null,
      format: "TV",
    });
    expect(state(wrapper).previewOpen).toBe(true);
    expect(state(wrapper).previewTitle).toBe("Sequel");
    wrapper.unmount();
  });
});

describe("concurrent detail edits", () => {
  it("keeps a confirmed rename while a delayed quick edit finishes", async () => {
    vi.mocked(movies.getMovie).mockResolvedValue(movie("a"));
    const slow = deferred<Movie>();
    vi.mocked(movies.patchMovie)
      .mockReturnValueOnce(slow.promise)
      .mockResolvedValueOnce(
        movie("a", {
          title: "Confirmed renamed title",
          favorite: true,
          ratingOverall: 7,
        }),
      );
    const wrapper = mountView(MovieDetail);
    await flushPromises();
    const vm = state(wrapper);
    const favorite = (vm.toggleFavorite as () => Promise<void>)();
    await flushPromises();
    (vm.onSaved as (saved: Movie) => void)(
      movie("a", { title: "Confirmed renamed title", favorite: true }),
    );
    const rating = (vm.onRatingChange as (value: number) => Promise<void>)(7);
    slow.resolve(movie("a", { favorite: true }));
    await Promise.all([favorite, rating]);
    expect(movies.patchMovie).toHaveBeenNthCalledWith(2, "a", {
      ratingOverall: 7,
    });
    expect(vm.movie).toMatchObject({
      title: "Confirmed renamed title",
      ratingOverall: 7,
    });
    wrapper.unmount();
  });
});
