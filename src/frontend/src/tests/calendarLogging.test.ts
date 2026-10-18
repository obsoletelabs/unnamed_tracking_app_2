// @vitest-environment happy-dom
import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { defineComponent, h } from "vue";
import { DEFAULT_PREFERENCES } from "../services/preferences";

const replace = vi.hoisted(() => vi.fn());
const route = vi.hoisted(() => ({
  path: "/calendar",
  query: {} as Record<string, unknown>,
}));
vi.mock("vue-router", () => ({
  useRoute: () => route,
  useRouter: () => ({ push: vi.fn(), replace }),
}));
const extras = vi.hoisted(() => ({
  createActivityEntry: vi.fn(),
}));
vi.mock("../services/mediaExtras", () => ({
  fetchCalendar: vi.fn(async () => []),
  fetchActivity: vi.fn(async () => []),
  fetchCalendarFeeds: vi.fn(async () => ({ combined: "", layers: {} })),
  fetchCalendarGames: vi.fn(async () => []),
  fetchCalendarEvents: vi.fn(async () => []),
  createCalendarEvent: vi.fn(),
  updateCalendarEvent: vi.fn(),
  deleteCalendarEvent: vi.fn(),
  createActivityEntry: extras.createActivityEntry,
  updateActivityEntry: vi.fn(),
  deleteActivityEntry: vi.fn(),
}));
vi.mock("../services/preferences", async (importOriginal) => {
  const actual =
    await importOriginal<typeof import("../services/preferences")>();
  return {
    ...actual,
    fetchPreferences: vi.fn(async () => ({ ...actual.DEFAULT_PREFERENCES })),
    queuePreferences: vi.fn(),
  };
});
const pages = vi.hoisted(() => ({
  movies: vi.fn(),
  shows: vi.fn(),
  anime: vi.fn(),
}));
vi.mock("../services/movies", () => ({
  fetchMovies: vi.fn(async () => []),
  fetchMoviesPage: pages.movies,
}));
vi.mock("../services/tvShows", () => ({
  fetchTVShows: vi.fn(async () => []),
  fetchTVShowsPage: pages.shows,
}));
vi.mock("../services/anime", () => ({
  fetchAnime: vi.fn(async () => []),
  fetchAnimePage: pages.anime,
}));

import { useCalendar } from "../composables/useCalendar";

function page(items: Array<Record<string, unknown>>) {
  return {
    items,
    total: items.length,
    offset: 0,
    limit: 8,
    statusCounts: {},
    scoreRanks: {},
  };
}

async function setup() {
  let api!: ReturnType<typeof useCalendar>;
  mount(
    defineComponent({
      setup() {
        api = useCalendar();
        return () => h("div");
      },
    }),
  );
  await flushPromises();
  return api;
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
  route.path = "/calendar";
  route.query = {};
  replace.mockReset();
  extras.createActivityEntry.mockReset();
  extras.createActivityEntry.mockImplementation(
    async (input: Record<string, unknown>) => ({
      id: "a1",
      mediaType: input.mediaType,
      mediaId: input.mediaId,
      mediaTitle: "x",
      eventType: input.eventType,
      eventDate: input.eventDate,
      count: input.count,
      detail: input.detail,
    }),
  );
  pages.movies.mockResolvedValue(page([]));
  pages.shows.mockResolvedValue(page([]));
  pages.anime.mockResolvedValue(page([]));
});
afterEach(() => vi.useRealTimers());

async function search(api: ReturnType<typeof useCalendar>, text: string) {
  api.manualSearch.value = text;
  await flushPromises();
  await vi.advanceTimersByTimeAsync(300);
  await flushPromises();
}

it("asks the server to search each media kind, so a title past page one is found", async () => {
  const api = await setup();
  // the server finds it however deep in the library it sits
  pages.anime.mockImplementation(
    async (_offset: number, _l: number, q: string) =>
      page(
        q === "Deep Title"
          ? [{ id: "deep", title: "Deep Title", posterUrl: null }]
          : [],
      ),
  );
  api.openManualForm();
  await search(api, "Deep Title");
  expect(pages.movies).toHaveBeenCalledWith(0, 8, "Deep Title");
  expect(pages.shows).toHaveBeenCalledWith(0, 8, "Deep Title");
  expect(api.manualPicker.state.value).toBe("results");
  expect(api.manualSearchResults.value).toEqual([
    {
      mediaType: "anime",
      mediaId: "deep",
      title: "Deep Title",
      posterUrl: null,
    },
  ]);
});

it("tells loading, empty and error apart", async () => {
  const api = await setup();
  api.openManualForm();
  api.manualSearch.value = "a";
  await flushPromises();
  expect(api.manualPicker.state.value).toBe("too-short");

  api.manualSearch.value = "nothing";
  await flushPromises();
  expect(api.manualPicker.state.value).toBe("loading");
  await vi.advanceTimersByTimeAsync(300);
  await flushPromises();
  expect(api.manualPicker.state.value).toBe("empty");

  pages.movies.mockRejectedValue(new Error("boom"));
  await search(api, "broken");
  expect(api.manualPicker.state.value).toBe("error");
  expect(api.manualPicker.error.value).toBe("boom");
});

it("keeps the chosen day when returning from the add-media flow", async () => {
  const api = await setup();
  api.openManualForm("2024-02-29");
  expect(api.addMediaLink.value.query.logDate).toBe("2024-02-29");

  route.query = { log: "2024-12-31", title: "New Show" };
  const again = await setup();
  expect(again.showManualForm.value).toBe(true);
  expect(again.manualDate.value).toBe("2024-12-31");
  expect(again.manualSearch.value).toBe("New Show");
  expect(replace).toHaveBeenCalledWith({ path: "/calendar" });
});

it("ignores a malformed return date", async () => {
  route.query = { log: "tomorrow" };
  const api = await setup();
  expect(api.showManualForm.value).toBe(false);
});

it("logs a movie as one watch and a show with its count and episode text", async () => {
  const api = await setup();
  api.openManualForm("2025-01-01");
  api.pickManualMedia({
    mediaType: "movie",
    mediaId: "m1",
    title: "Film",
    posterUrl: null,
  });
  api.manualCount.value = 5;
  await flushPromises();
  expect(api.manualIsMovie.value).toBe(true);
  expect(api.manualEffect.value).toContain("one watch");
  expect(api.manualEffect.value).toContain(
    "progress on the title is not changed",
  );
  await api.submitManualEntry();
  expect(extras.createActivityEntry).toHaveBeenLastCalledWith(
    expect.objectContaining({
      mediaType: "movie",
      count: 1,
      eventDate: "2025-01-01",
    }),
  );

  api.openManualForm("2025-01-02");
  api.pickManualMedia({
    mediaType: "tv",
    mediaId: "t1",
    title: "Show",
    posterUrl: null,
  });
  api.manualCount.value = 2;
  api.manualDetail.value = " S2E9 then S2E5 ";
  await api.submitManualEntry();
  expect(extras.createActivityEntry).toHaveBeenLastCalledWith({
    mediaType: "tv",
    mediaId: "t1",
    eventType: "episodes_watched",
    eventDate: "2025-01-02",
    count: 2,
    detail: "S2E9 then S2E5",
  });
});

it("rejects a missing date or a non-positive episode count", async () => {
  const api = await setup();
  api.openManualForm("2025-01-01");
  api.pickManualMedia({
    mediaType: "tv",
    mediaId: "t1",
    title: "S",
    posterUrl: null,
  });
  api.manualCount.value = 0;
  await api.submitManualEntry();
  expect(api.manualError.value).toMatch(/episodes/);
  api.manualCount.value = 1;
  api.manualDate.value = "";
  await api.submitManualEntry();
  expect(api.manualError.value).toBe("Pick a date.");
  expect(extras.createActivityEntry).not.toHaveBeenCalled();
});

it("sends a repeated submission only once while saving", async () => {
  const api = await setup();
  api.openManualForm("2025-03-10");
  api.pickManualMedia({
    mediaType: "tv",
    mediaId: "t1",
    title: "S",
    posterUrl: null,
  });
  const first = api.submitManualEntry();
  const second = api.submitManualEntry();
  await Promise.all([first, second]);
  expect(extras.createActivityEntry).toHaveBeenCalledTimes(1);
});

it("keeps the local calendar day at the end of the year", async () => {
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date"] });
  vi.setSystemTime(new Date(2024, 11, 31, 23, 59, 0));
  const api = await setup();
  api.openManualForm();
  expect(api.manualDate.value).toBe("2024-12-31");
  expect(DEFAULT_PREFERENCES).toBeDefined();
});
