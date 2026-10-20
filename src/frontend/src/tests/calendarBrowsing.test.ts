// @vitest-environment happy-dom
import { flushPromises, mount } from "@vue/test-utils";
import { defineComponent, h, nextTick } from "vue";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

const router = vi.hoisted(() => ({ push: vi.fn() }));
vi.mock("vue-router", () => ({
  useRouter: () => router,
  useRoute: () => ({ query: {} }),
}));
const extras = vi.hoisted(() => ({
  fetchCalendar: vi.fn(),
  fetchCalendarRange: vi.fn(),
  fetchActivity: vi.fn(),
  fetchCalendarFeeds: vi.fn(),
  fetchCalendarGames: vi.fn(),
  fetchCalendarEvents: vi.fn(),
  createCalendarEvent: vi.fn(),
  updateCalendarEvent: vi.fn(),
  deleteCalendarEvent: vi.fn(),
  createActivityEntry: vi.fn(),
  updateActivityEntry: vi.fn(),
  deleteActivityEntry: vi.fn(),
}));
vi.mock("../services/mediaExtras", () => extras);
vi.mock("../services/calendarSources", () => ({
  fetchCalendarSources: vi.fn(async () => ({
    remote_enabled: true,
    sources: [],
  })),
  fetchSourceEvents: vi.fn(),
}));
const prefs = vi.hoisted(() => ({ value: {} as Record<string, unknown> }));
vi.mock("../services/preferences", async (original) => ({
  ...(await original<typeof import("../services/preferences")>()),
  fetchPreferences: vi.fn(async () => ({ ...prefs.value })),
  queuePreferences: vi.fn(async (changes: Record<string, unknown>) => {
    prefs.value = { ...prefs.value, ...changes };
    return { prefs: { ...prefs.value }, latest: true };
  }),
}));
const library = vi.hoisted(() => ({
  movies: [] as unknown[],
  shows: [] as unknown[],
  anime: [] as unknown[],
  fail: false,
}));
vi.mock("../services/movies", () => ({
  fetchMovies: vi.fn(async () => {
    if (library.fail) throw new Error("library unavailable");
    return library.movies;
  }),
}));
vi.mock("../services/tvShows", () => ({
  fetchTVShows: vi.fn(async () => library.shows),
}));
vi.mock("../services/anime", () => ({
  fetchAnime: vi.fn(async () => library.anime),
}));

import { useCalendar } from "../composables/useCalendar";
import { DEFAULT_PREFERENCES } from "../services/preferences";
import { dayKey } from "../utils/calendarWeeks";
import {
  fetchCalendarSources,
  fetchSourceEvents,
} from "../services/calendarSources";

type Calendar = ReturnType<typeof useCalendar>;
async function setup(): Promise<{ cal: Calendar; unmount: () => void }> {
  let cal!: Calendar;
  const wrapper = mount(
    defineComponent({
      setup() {
        cal = useCalendar();
        return () => h("div");
      },
    }),
  );
  await flushPromises();
  return { cal, unmount: () => wrapper.unmount() };
}

const entry = (title: string, airAt: number, n: number | null = null) => ({
  mediaType: "tv",
  mediaId: title,
  title,
  posterUrl: null,
  nextEpisodeNumber: n,
  airAt,
  kind: "episode",
  isProjected: true,
});

const mounted: (() => void)[] = [];
beforeEach(() => {
  vi.mocked(fetchCalendarSources).mockResolvedValue({
    remote_enabled: true,
    sources: [],
  });
  vi.setSystemTime(new Date(2026, 9, 18, 12));
  prefs.value = { ...DEFAULT_PREFERENCES };
  library.movies = [
    { id: "m1", title: "Dune", posterUrl: null },
    { id: "m2", title: "Alien", posterUrl: null },
  ];
  library.shows = [{ id: "s1", title: "Severance", posterUrl: null }];
  library.anime = [];
  library.fail = false;
  extras.fetchCalendar.mockResolvedValue([]);
  extras.fetchCalendarRange.mockResolvedValue([]);
  extras.fetchActivity.mockResolvedValue([]);
  extras.fetchCalendarGames.mockResolvedValue([]);
  extras.fetchCalendarEvents.mockResolvedValue([]);
  extras.fetchCalendarFeeds.mockResolvedValue({
    combined: "https://t.example/api/calendar/feed/TOK.ics",
    anime: "https://t.example/api/calendar/feed/TOK/anime.ics",
    tv: "https://t.example/api/calendar/feed/TOK/tv.ics",
    movie: "https://t.example/api/calendar/feed/TOK/movie.ics",
    game: "https://t.example/api/calendar/feed/TOK/game.ics",
    manual: "https://t.example/api/calendar/feed/TOK/manual.ics",
  });
  extras.createActivityEntry.mockImplementation(async (input) => ({
    id: "a1",
    mediaTitle: "x",
    ...input,
  }));
  try {
    sessionStorage.clear();
  } catch {
    /* none */
  }
});
afterEach(() => {
  mounted.splice(0).forEach((fn) => fn());
  vi.clearAllMocks();
  vi.useRealTimers();
});
async function open() {
  const made = await setup();
  mounted.push(made.unmount);
  return made.cal;
}

it("fetches only the months you browse to beyond the first 90 days, once each (#251)", async () => {
  const cal = await open();
  // the first request covers 90 days; nothing extra is asked for near months
  expect(extras.fetchCalendar).toHaveBeenCalledWith(90);
  expect(extras.fetchCalendarRange).not.toHaveBeenCalled();
  cal.nextMonth(); // Nov
  cal.nextMonth(); // Dec
  await flushPromises();
  expect(extras.fetchCalendarRange).not.toHaveBeenCalled();

  extras.fetchCalendarRange.mockResolvedValue([
    entry("Far show", Date.UTC(2027, 1, 10) / 1000, 9),
  ]);
  cal.nextMonth(); // Jan 2027
  cal.nextMonth(); // Feb 2027: a grid that reaches past the loaded window
  await flushPromises();
  const calls = extras.fetchCalendarRange.mock.calls;
  expect(calls.length).toBeGreaterThan(0);
  for (const [from, to] of calls) {
    expect(from <= to).toBe(true);
    expect(
      (Date.parse(to) - Date.parse(from)) / 86_400_000,
    ).toBeLessThanOrEqual(91);
  }
  expect(cal.rangeState.value).toBe("idle");
  expect(
    cal.gridCells.value.some((c) =>
      c.items.some((i) => i.title === "Far show"),
    ),
  ).toBe(true);

  // going back and forth does not ask again for a month it already has
  const before = extras.fetchCalendarRange.mock.calls.length;
  cal.prevMonth();
  cal.nextMonth();
  await flushPromises();
  expect(extras.fetchCalendarRange.mock.calls.length).toBe(before);
  expect(cal.monthIsFar.value).toBe(true);
});

it("shows a failure for a far month and recovers on retry without losing what was loaded (#251)", async () => {
  const cal = await open();
  extras.fetchCalendar.mockResolvedValue([
    entry("Near", Date.UTC(2026, 9, 25) / 1000, 3),
  ]);
  await cal.refreshAll();
  for (let i = 0; i < 5; i++) cal.nextMonth();
  extras.fetchCalendarRange.mockRejectedValueOnce(
    new Error("Calendar service is busy"),
  );
  cal.nextMonth();
  await flushPromises();
  expect(cal.rangeState.value).toBe("error");
  expect(cal.rangeError.value).toBe("Calendar service is busy");
  extras.fetchCalendarRange.mockResolvedValue([]);
  cal.retryRange();
  await flushPromises();
  expect(cal.rangeState.value).toBe("idle");
  expect(cal.entries.value.map((e) => e.title)).toEqual(["Near"]);
});

it("stops at about a year ahead", async () => {
  const cal = await open();
  let guard = 0;
  while (cal.canGoNext.value && guard++ < 30) cal.nextMonth();
  expect(guard).toBeGreaterThan(10);
  expect(guard).toBeLessThan(16);
  expect(cal.canGoNext.value).toBe(false);
});

it("saves the week start per account and rotates the grid (#251)", async () => {
  const cal = await open();
  expect(cal.WEEKDAY_LABELS.value[0]).toBe("Sun");
  await cal.setWeekStart(4);
  await flushPromises();
  expect(prefs.value.calendar_week_start).toBe(4);
  expect(cal.WEEKDAY_LABELS.value[0]).toBe("Thu");
  expect(new Date(cal.gridCells.value[0].key + "T00:00:00").getDay()).toBe(4);
  expect(cal.gridCells.value.length % 7).toBe(0);
});

it("offers one subscription link per layer, all from the same secret (#257)", async () => {
  const cal = await open();
  await cal.openFeed();
  expect(cal.feedUrl.value).toMatch(/feed\/TOK\.ics$/);
  expect(cal.FEED_CHOICES.map((c) => c.key)).toEqual([
    "combined",
    "anime",
    "tv",
    "movie",
    "game",
    "manual",
  ]);
  for (const choice of cal.FEED_CHOICES.slice(1)) {
    cal.feedChoice.value = choice.key;
    expect(cal.feedUrl.value).toBe(
      `https://t.example/api/calendar/feed/TOK/${choice.key}.ics`,
    );
  }
});

it("logs the exact episodes watched, in any order, as history only (#250)", async () => {
  const cal = await open();
  await cal.openManualForm("2026-10-02");
  cal.manualSearch.value = "sever";
  await nextTick();
  expect(cal.manualSearchState.value).toBe("results");
  cal.pickManualMedia({
    mediaType: "tv",
    mediaId: "s1",
    title: "Severance",
    posterUrl: null,
  });
  cal.manualEpisodes.value = "7 5-6 3";
  await nextTick();
  expect(cal.manualSummary.value).toContain("episodes 3, 5-7 of Severance");
  expect(cal.manualSummary.value).toContain(
    "does not change your watched progress",
  );
  await cal.submitManualEntry();
  expect(extras.createActivityEntry).toHaveBeenCalledWith({
    mediaType: "tv",
    mediaId: "s1",
    eventType: "episodes_watched",
    eventDate: "2026-10-02",
    count: 4,
    detail: "Episodes 3, 5-7",
  });
});

it("refuses an unreadable episode list and treats a movie as one viewing", async () => {
  const cal = await open();
  await cal.openManualForm("2026-10-02");
  cal.pickManualMedia({
    mediaType: "tv",
    mediaId: "s1",
    title: "Severance",
    posterUrl: null,
  });
  cal.manualEpisodes.value = "five";
  await nextTick();
  expect(cal.manualEpisodeError.value).toContain("not an episode number");
  await cal.submitManualEntry();
  expect(extras.createActivityEntry).not.toHaveBeenCalled();

  cal.pickManualMedia({
    mediaType: "movie",
    mediaId: "m1",
    title: "Dune",
    posterUrl: null,
  });
  cal.manualEpisodes.value = "";
  cal.manualCount.value = 9;
  await nextTick();
  expect(cal.manualIsMovie.value).toBe(true);
  await cal.submitManualEntry();
  expect(extras.createActivityEntry).toHaveBeenCalledWith(
    expect.objectContaining({ mediaType: "movie", count: 1, detail: null }),
  );
});

it("tells loading, failure and no-match apart in the title search (#250)", async () => {
  library.fail = true;
  const cal = await open();
  const opening = cal.openManualForm("2026-10-02");
  cal.manualSearch.value = "dune";
  await opening;
  expect(cal.manualSearchState.value).toBe("error");
  expect(cal.manualLibraryError.value).toBe("library unavailable");
  library.fail = false;
  cal.retryManualLibrary();
  await flushPromises();
  expect(cal.manualSearchState.value).toBe("results");
  cal.manualSearch.value = "no such title";
  await nextTick();
  expect(cal.manualSearchState.value).toBe("none");
});

it("keeps the chosen day while a missing title is added, then reopens the form (#250)", async () => {
  const cal = await open();
  await cal.openManualForm("2026-09-30");
  cal.manualSearch.value = "Brand New Show";
  cal.addMissingTitle();
  expect(router.push).toHaveBeenCalledWith({
    path: "/media/search",
    query: { q: "Brand New Show" },
  });
  expect(cal.showManualForm.value).toBe(false);
  expect(
    JSON.parse(sessionStorage.getItem("calendar-pending-log-v1")!),
  ).toMatchObject({
    date: "2026-09-30",
    search: "Brand New Show",
  });
  // coming back to the calendar reopens the form on that day with the title typed
  const again = await open();
  await flushPromises();
  expect(again.showManualForm.value).toBe(true);
  expect(again.manualDate.value).toBe("2026-09-30");
  expect(again.manualSearch.value).toBe("Brand New Show");
  expect(sessionStorage.getItem("calendar-pending-log-v1")).toBeNull();
  expect(dayKey(new Date(2026, 8, 30))).toBe("2026-09-30");
});

it("ignores a stale or malformed pending log", async () => {
  sessionStorage.setItem(
    "calendar-pending-log-v1",
    JSON.stringify({
      date: "2026-09-30",
      search: "x",
      at: Date.now() - 3 * 3_600_000,
    }),
  );
  const stale = await open();
  expect(stale.showManualForm.value).toBe(false);
  sessionStorage.setItem("calendar-pending-log-v1", "{nope");
  const junk = await open();
  expect(junk.showManualForm.value).toBe(false);
});

it("names a layer that failed to load instead of looking empty, and keeps what it had (#262)", async () => {
  extras.fetchCalendarEvents.mockResolvedValueOnce([
    {
      id: "e1",
      title: "Mine",
      eventDate: "2026-10-20",
      eventTime: null,
      note: null,
      mediaType: null,
      mediaId: null,
    },
  ]);
  const cal = await open();
  expect(cal.failedLayers.value).toEqual([]);
  extras.fetchCalendarEvents.mockRejectedValueOnce(new Error("down"));
  extras.fetchCalendarGames.mockRejectedValueOnce(new Error("down"));
  await cal.refreshAll();
  expect(cal.failedLayers.value).toEqual(["games history", "your own entries"]);
  // what was loaded before is still there
  expect(
    cal.gridCells.value.some((c) => c.items.some((i) => i.title === "Mine")),
  ).toBe(true);
  await cal.retryFailedLayers();
  await flushPromises();
  expect(cal.failedLayers.value).toEqual([]);
});

it("keeps subscribed events separate from the manual-entry filter and opens their own editor", async () => {
  const source = {
    id: "s1",
    name: "Film club",
    hostname: "calendar.example",
    color: "#123456",
    enabled: true,
    refresh_minutes: 360,
    last_attempt_at: 1,
    last_success_at: 1,
    next_refresh_at: 2,
    last_status: "updated",
    consecutive_failures: 0,
    warnings: [],
  };
  vi.mocked(fetchCalendarSources).mockResolvedValueOnce({
    remote_enabled: true,
    sources: [source],
  });
  vi.mocked(fetchSourceEvents).mockResolvedValueOnce({
    events: [
      {
        event_id: "e1",
        uid: "u1",
        recurrence_key: "",
        title: "External party",
        note: "",
        location: "Cinema",
        url: "",
        start: { kind: "date", value: "2026-10-19" },
        original_start: { kind: "date", value: "2026-10-19" },
        duration_seconds: 86400,
        event_date: "2026-10-19",
        event_time: null,
        end_date: "2026-10-20",
        end_time: null,
        last_date: "2026-10-19",
        recurring: false,
        series_override: false,
        occurrence_override: false,
      },
    ],
    warnings: [],
  });
  const cal = await open();
  cal.filters.event = false;
  await nextTick();
  const item = cal.gridCells.value
    .flatMap((day) => day.items)
    .find((entry) => entry.title === "External party");
  expect(item?.layer).toBe("external");
  cal.activate(item!);
  expect(cal.selectedExternal.value?.event_id).toBe("e1");
  cal.filters.external = false;
  await nextTick();
  expect(
    cal.gridCells.value.some((day) =>
      day.items.some((entry) => entry.title === "External party"),
    ),
  ).toBe(false);
});
