// @vitest-environment happy-dom
import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { defineComponent, h } from "vue";
import { DEFAULT_PREFERENCES } from "../services/preferences";

vi.mock("vue-router", () => ({
  useRoute: () => ({ path: "/calendar", query: {} }),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}));
const extras = vi.hoisted(() => ({ fetchCalendar: vi.fn() }));
vi.mock("../services/mediaExtras", () => ({
  fetchCalendar: extras.fetchCalendar,
  fetchActivity: vi.fn(async () => []),
  fetchCalendarFeeds: vi.fn(async () => ({ combined: "", layers: {} })),
  fetchCalendarGames: vi.fn(async () => []),
  fetchCalendarEvents: vi.fn(async () => []),
  createCalendarEvent: vi.fn(),
  updateCalendarEvent: vi.fn(),
  deleteCalendarEvent: vi.fn(),
  createActivityEntry: vi.fn(),
  updateActivityEntry: vi.fn(),
  deleteActivityEntry: vi.fn(),
}));
const prefs = vi.hoisted(() => ({
  weekStart: 0,
  queue: vi.fn(),
}));
vi.mock("../services/preferences", async (importOriginal) => {
  const actual =
    await importOriginal<typeof import("../services/preferences")>();
  return {
    ...actual,
    fetchPreferences: vi.fn(async () => ({
      ...actual.DEFAULT_PREFERENCES,
      calendar_week_start: prefs.weekStart,
    })),
    queuePreferences: prefs.queue,
  };
});
vi.mock("../services/movies", () => ({
  fetchMovies: vi.fn(async () => []),
  fetchMoviesPage: vi.fn(),
}));
vi.mock("../services/tvShows", () => ({
  fetchTVShows: vi.fn(async () => []),
  fetchTVShowsPage: vi.fn(),
}));
vi.mock("../services/anime", () => ({
  fetchAnime: vi.fn(async () => []),
  fetchAnimePage: vi.fn(),
}));

import { useCalendar } from "../composables/useCalendar";

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
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date(2028, 0, 15, 12, 0, 0));
  prefs.weekStart = 0;
  prefs.queue.mockReset();
  prefs.queue.mockResolvedValue({ latest: true });
  extras.fetchCalendar.mockReset();
  extras.fetchCalendar.mockResolvedValue([]);
});
afterEach(() => vi.useRealTimers());

const DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

it.each([0, 1, 2, 3, 4, 5, 6])(
  "lays out month and week with week start %i",
  async (start) => {
    prefs.weekStart = start;
    const api = await setup();
    expect(api.WEEKDAY_LABELS.value[0]).toBe(DAYS[start]);
    expect(api.WEEKDAY_LABELS.value).toHaveLength(7);
    // February 2028 has a leap day and starts on a Tuesday
    api.viewYear.value = 2028;
    api.viewMonth.value = 1;
    await flushPromises();
    const cells = api.gridCells.value;
    expect(cells.length % 7).toBe(0);
    const firstOfMonth = cells.findIndex((c) => c.inMonth);
    expect(firstOfMonth).toBe((2 - start + 7) % 7);
    expect(cells.filter((c) => c.inMonth)).toHaveLength(29);
    expect(cells.some((c) => c.key === "2028-02-29" && c.inMonth)).toBe(true);
    // the week view starts on the chosen day too
    api.calView.value = "week";
    await flushPromises();
    const week = api.weekDays.value;
    expect(week).toHaveLength(7);
    const weekdayOf = (key: string) => new Date(`${key}T00:00:00`).getDay();
    expect(weekdayOf(week[0].key)).toBe(start);
  },
);

it("saves a week start choice and puts the old one back if saving fails", async () => {
  const api = await setup();
  await api.setWeekStart(4);
  expect(prefs.queue).toHaveBeenCalledWith({ calendar_week_start: 4 });
  expect(api.prefs.value.calendar_week_start).toBe(4);
  prefs.queue.mockRejectedValueOnce(new Error("offline"));
  await api.setWeekStart(2);
  expect(api.prefs.value.calendar_week_start).toBe(4);
});

it("only loads the first 90 days until you navigate beyond them", async () => {
  const api = await setup();
  expect(extras.fetchCalendar).toHaveBeenCalledTimes(1);
  expect(extras.fetchCalendar).toHaveBeenCalledWith(90);
  api.viewYear.value = 2028;
  api.viewMonth.value = 1; // February is inside the first 90 days
  await flushPromises();
  expect(extras.fetchCalendar).toHaveBeenCalledTimes(1);
  expect(api.rangeNote.value).toBeNull();
});

it("requests a bounded range for a far month and remembers it", async () => {
  const api = await setup();
  extras.fetchCalendar.mockResolvedValueOnce([
    {
      mediaType: "movie",
      mediaId: "m1",
      title: "Far film",
      posterUrl: null,
      nextEpisodeNumber: null,
      airAt: Date.UTC(2028, 8, 15, 12) / 1000,
      kind: "release",
      isProjected: false,
    },
  ]);
  api.viewYear.value = 2028;
  api.viewMonth.value = 8; // September 2028, well past 90 days
  await flushPromises();
  const call = extras.fetchCalendar.mock.calls.at(-1)!;
  expect(call[1]).toBeDefined();
  const { start, end } = call[1] as { start: string; end: string };
  const span = (Date.parse(end) - Date.parse(start)) / 86_400_000;
  expect(span).toBeGreaterThan(30);
  expect(span).toBeLessThan(100);
  expect(
    api.gridCells.value.find((c) => c.key === "2028-09-15")?.items,
  ).toHaveLength(1);
  expect(api.rangeNote.value).toMatch(/mostly unannounced/);

  const before = extras.fetchCalendar.mock.calls.length;
  api.viewMonth.value = 9;
  await flushPromises();
  api.viewMonth.value = 8;
  await flushPromises();
  // October loaded once; September came from memory
  expect(extras.fetchCalendar.mock.calls.length).toBe(before + 1);
});

it("pads a range across a year boundary so time zones cannot drop a day", async () => {
  const api = await setup();
  api.viewYear.value = 2028;
  api.viewMonth.value = 11; // December 2028
  await flushPromises();
  const { start, end } = extras.fetchCalendar.mock.calls.at(-1)![1] as {
    start: string;
    end: string;
  };
  expect(start <= "2028-11-26").toBe(true);
  expect(end >= "2029-01-06").toBe(true);
});

it("shows a past-dates note and fetches the past range", async () => {
  const api = await setup();
  api.viewYear.value = 2027;
  api.viewMonth.value = 5;
  await flushPromises();
  expect(extras.fetchCalendar.mock.calls.at(-1)![1]).toBeDefined();
  expect(api.rangeNote.value).toMatch(/Past dates/);
});

it("reports a failed range load and retries it", async () => {
  const api = await setup();
  extras.fetchCalendar.mockRejectedValueOnce(new Error("range failed"));
  api.viewYear.value = 2028;
  api.viewMonth.value = 8;
  await flushPromises();
  expect(api.rangeError.value).toBe("range failed");
  await api.ensureRange();
  expect(api.rangeError.value).toBeNull();
});

it("limits navigation to two years ahead and ten years back", async () => {
  const api = await setup();
  api.viewYear.value = 2029;
  api.viewMonth.value = 11; // December 2029: one more month is allowed
  expect(api.canGoNext.value).toBe(true);
  api.viewYear.value = 2030;
  api.viewMonth.value = 0; // January 2030 holds the last allowed day
  expect(api.canGoNext.value).toBe(false);
  expect(api.canGoPrev.value).toBe(true);
  api.viewYear.value = 2018;
  api.viewMonth.value = 0;
  expect(api.canGoPrev.value).toBe(false);
  expect(api.jumpMonthMax).toBe("2030-01");
  expect(api.jumpMonthMin).toBe("2018-01");
});

it("keeps the default preferences compatible", () => {
  expect(DEFAULT_PREFERENCES.calendar_week_start).toBe(0);
});
