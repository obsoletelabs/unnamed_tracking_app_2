// @vitest-environment happy-dom
import { flushPromises, mount, type VueWrapper } from "@vue/test-utils";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ref } from "vue";
import TVShowLibrary from "../views/TVShowLibrary.vue";
import type { TVShow, Season } from "../types/tv_show";
import type { TVShowInput, SeasonUpdateInput } from "../services/tvShows";

const services = vi.hoisted(() => ({
  updateTVShow: vi.fn(),
  updateSeason: vi.fn(),
}));
vi.mock("../services/tvShows", () => ({
  ...services,
  fetchTVShowsPage: vi.fn(),
  tvLibraryRevision: () => 0,
  deleteTVShow: vi.fn(),
  tvShowToInput: (show: TVShow) => ({ title: show.title }),
}));
vi.mock("vue-router", () => ({
  useRoute: () => ({ path: "/tv", query: {} }),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}));
vi.mock("../utils/useKeptAlive", () => ({ useKeptAlive: vi.fn() }));
vi.mock("../composables/useMediaLibraryPage", () => ({
  useMediaLibraryPage: () => state,
}));
const state = {
  entries: ref<TVShow[]>([]),
  loading: ref(false),
  refreshing: ref(false),
  error: ref(null),
  total: ref(1),
  statusCounts: ref({}),
  scoreRanks: ref({}),
  currentFilters: ref({}),
  hasMore: ref(false),
  load: vi.fn(),
  loadMore: vi.fn(),
  refreshLibrary: vi.fn(),
};
const wrappers: VueWrapper[] = [];
function season(
  number: number,
  watched: number,
  count: number | null = 10,
): Season {
  return {
    id: "season-" + number,
    showId: "show",
    seasonNumber: number,
    name: null,
    episodeCount: count,
    episodesWatched: watched,
    status: "in progress",
    airDate: null,
    posterUrl: null,
    episodes: [],
    createdAt: "",
    updatedAt: "",
  };
}
function show(): TVShow {
  return {
    id: "show",
    userId: "user",
    title: "Two seasons",
    sortTitle: "two seasons",
    description: null,
    firstAirDate: null,
    episodeRuntimeMinutes: null,
    creators: [],
    studios: [],
    countries: [],
    languages: [],
    genres: [],
    tags: [],
    features: [],
    ageRating: null,
    tmdbScore: null,
    source: null,
    externalId: null,
    posterUrl: null,
    backdropUrl: null,
    status: "in progress",
    priority: null,
    favorite: false,
    rewatches: 0,
    note: null,
    startDate: null,
    endDate: null,
    ratingStory: null,
    ratingPerformance: null,
    ratingSoundtrack: null,
    ratingOverall: 8,
    personalRank: null,
    lockedFields: [],
    seasons: [season(1, 10), season(2, 3)],
    createdAt: "",
    updatedAt: "",
    isAiring: null,
    nextEpisodeAirAt: null,
    nextEpisodeNumber: null,
    airingIntervalDays: null,
  };
}
beforeEach(() => {
  localStorage.clear();
  state.entries.value = [show()];
  services.updateTVShow
    .mockReset()
    .mockImplementation(async (_id: string, input: TVShowInput) => ({
      ...state.entries.value[0]!,
      ...input,
    }));
  services.updateSeason
    .mockReset()
    .mockImplementation(
      async (_show: string, id: string, input: SeasonUpdateInput) => ({
        ...state.entries.value[0]!,
        seasons: state.entries.value[0]!.seasons.map((s) =>
          s.id === id ? { ...s, ...input } : s,
        ),
      }),
    );
});
afterEach(() => wrappers.splice(0).forEach((wrapper) => wrapper.unmount()));
async function editor() {
  const wrapper = mount(TVShowLibrary, {
    global: {
      stubs: {
        UiModal: { template: "<div><slot/><slot name='footer'/></div>" },
        RouterLink: true,
        MediaTopBar: true,
      },
    },
  });
  wrappers.push(wrapper);
  await flushPromises();
  await wrapper.get('button[title="Edit"]').trigger("click");
  return wrapper;
}
function field(wrapper: VueWrapper, name: string) {
  return wrapper
    .findAll("label.qa-field")
    .find((label) => label.get("span").text() === name)!
    .get("input, select");
}
async function save(wrapper: VueWrapper) {
  await wrapper
    .findAll("button")
    .find((button) => button.text() === "Save")!
    .trigger("click");
  await flushPromises();
}
it("does not rewrite season counts on a rating-only save", async () => {
  const wrapper = await editor();
  await field(wrapper, "Your rating (0–10)").setValue("9");
  await save(wrapper);
  expect(services.updateSeason).not.toHaveBeenCalled();
  expect(state.entries.value[0]!.seasons.map((s) => s.episodeCount)).toEqual([
    10, 10,
  ]);
});
it("labels the edited season and uses its values while retaining the show summary", async () => {
  const wrapper = await editor();
  expect(wrapper.text()).toContain("Season 2");
  expect(
    (field(wrapper, "Episodes watched").element as HTMLInputElement).value,
  ).toBe("3");
  expect(
    (field(wrapper, "Total episodes").element as HTMLInputElement).value,
  ).toBe("10");
  expect(wrapper.text()).toContain("13/20");
  await field(wrapper, "Total episodes").setValue("12");
  await field(wrapper, "Episodes watched").setValue("4");
  await save(wrapper);
  expect(services.updateSeason).toHaveBeenCalledWith("show", "season-2", {
    episodeCount: 12,
    episodesWatched: 4,
  });
});
it("retains the edited season even if the current season changes before saving", async () => {
  const wrapper = await editor();
  state.entries.value[0]!.seasons[1]!.episodesWatched = 10;
  state.entries.value[0]!.seasons.push(season(3, 0));
  await field(wrapper, "Total episodes").setValue("11");
  await save(wrapper);
  expect(services.updateSeason).toHaveBeenCalledWith("show", "season-2", {
    episodeCount: 11,
  });
});
it("keeps unknown episode counts on an unrelated save", async () => {
  state.entries.value[0]!.seasons[1]!.episodeCount = null;
  const wrapper = await editor();
  await field(wrapper, "Your rating (0–10)").setValue("9");
  await save(wrapper);
  expect(services.updateSeason).not.toHaveBeenCalled();
});
it("uses the last season once caught up and preserves rewatch progress on a rating save", async () => {
  state.entries.value[0]!.status = "watched";
  state.entries.value[0]!.seasons[1]!.episodesWatched = 12;
  const wrapper = await editor();
  expect(wrapper.text()).toContain("Season 2");
  await field(wrapper, "Your rating (0–10)").setValue("9");
  await save(wrapper);
  expect(services.updateSeason).not.toHaveBeenCalled();
});
it("does not offer episode edits when a TV show has no seasons", async () => {
  state.entries.value[0]!.seasons = [];
  const wrapper = await editor();
  expect(
    wrapper.findAll("label.qa-field").map((label) => label.text()),
  ).not.toContain("Total episodes");
  await field(wrapper, "Your rating (0–10)").setValue("9");
  await save(wrapper);
  expect(services.updateSeason).not.toHaveBeenCalled();
});

it("preserves season data on status and date edits", async () => {
  const wrapper = await editor();
  await field(wrapper, "Status").setValue("hold");
  await field(wrapper, "Started").setValue("2026-10-01");
  await save(wrapper);
  expect(services.updateSeason).not.toHaveBeenCalled();
});
it("clears a season count explicitly without clearing another season", async () => {
  const wrapper = await editor();
  await field(wrapper, "Total episodes").setValue("");
  await save(wrapper);
  expect(services.updateSeason).toHaveBeenCalledWith("show", "season-2", {
    episodeCount: null,
  });
});
it("does not reuse show-wide episode totals when saving the completion rating", async () => {
  state.entries.value[0]!.ratingOverall = null;
  const wrapper = await editor();
  await field(wrapper, "Status").setValue("completed");
  await save(wrapper);
  expect(services.updateSeason).toHaveBeenCalledWith("show", "season-2", {
    episodesWatched: 10,
  });
  services.updateSeason.mockClear();
  await wrapper.get('input[aria-label="Completion rating"]').setValue("9");
  await wrapper
    .findAll("button")
    .find((button) => button.text() === "Save rating")!
    .trigger("click");
  await flushPromises();
  expect(services.updateSeason).not.toHaveBeenCalled();
  expect(state.entries.value[0]!.seasons.map((s) => s.episodeCount)).toEqual([
    10, 10,
  ]);
});
