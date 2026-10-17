// @vitest-environment happy-dom
import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import ActivityHeatmap from "../components/stats/ActivityHeatmap.vue";
import {
  addDays,
  describeCounts,
  itemPath,
  monthMarks,
} from "../utils/activityDays";
import type { ActivityDay } from "../services/mediaStats";

const fetchMock =
  vi.fn<(url: string, init: RequestInit) => Promise<Response>>();
const stubs = {
  RouterLink: { props: ["to"], template: '<a :href="to"><slot/></a>' },
  UiModal: {
    props: ["title", "description"],
    emits: ["close"],
    template:
      '<div role="dialog"><h2>{{ title }}</h2><p>{{ description }}</p><slot/><slot name="footer"/></div>',
  },
};
const props = {
  start: "2025-10-12",
  end: "2026-10-16",
  timezone: "Pacific/Kiritimati",
  days: [
    { date: "2026-10-14", count: 4, episodes: 3, achievements: 1, history: 0 },
    { date: "2026-10-15", count: 1, episodes: 0, achievements: 1, history: 2 },
  ],
};

function day(overrides: Partial<ActivityDay> = {}): ActivityDay {
  return {
    date: "2026-10-14",
    timezone: "Pacific/Kiritimati",
    count: 4,
    episodes: 3,
    achievements: 1,
    history: 0,
    starts_at: 0,
    ends_at: 86400,
    total: 2,
    offset: 0,
    limit: 50,
    next_offset: 2,
    complete: true,
    items: [
      {
        kind: "episodes",
        media_type: "tv",
        id: "show-1",
        title: "Show",
        count: 3,
      },
      {
        kind: "achievement",
        id: "ach-1",
        name: "First",
        unlocked_at: 1,
        game_id: "game-1",
        game_title: "Game",
      },
    ],
    ...overrides,
  };
}
function respond(body: ActivityDay) {
  fetchMock.mockResolvedValueOnce(new Response(JSON.stringify(body)));
}
function mountHeatmap() {
  return mount(ActivityHeatmap, {
    props,
    global: { stubs },
    attachTo: document.body,
  });
}

// 370 real buttons per mount are slow under happy-dom on a busy runner
vi.setConfig({ testTimeout: 30_000 });

beforeEach(() => vi.stubGlobal("fetch", fetchMock));
afterEach(() => {
  fetchMock.mockReset();
  vi.unstubAllGlobals();
  document.body.innerHTML = "";
});

describe("activity heatmap grid", () => {
  it("renders the server window as whole Sunday-first weeks with month labels", () => {
    const wrapper = mountHeatmap();
    const cells = wrapper.findAll("button.heat-cell");
    expect(cells[0]!.attributes("data-day")).toBe("2025-10-12");
    expect(cells.at(-1)!.attributes("data-day")).toBe("2026-10-16");
    expect(cells).toHaveLength(370);
    const labels = wrapper.findAll(".heat-month").map((m) => m.text());
    expect(labels.filter(Boolean)).toContain("Jan");
    expect(labels.filter(Boolean).length).toBeGreaterThanOrEqual(12);
  });

  it("gives every day an accessible name with its exact counts", () => {
    const wrapper = mountHeatmap();
    const busy = wrapper.find('[data-day="2026-10-14"]');
    expect(busy.attributes("aria-label")).toContain(
      "3 episodes, 1 achievement",
    );
    expect(
      wrapper.find('[data-day="2026-10-13"]').attributes("aria-label"),
    ).toContain("nothing");
    expect(busy.attributes("title")).toBe(busy.attributes("aria-label"));
  });

  it("keeps one square in the tab order and moves it with the arrow keys", async () => {
    const wrapper = mountHeatmap();
    const tabbable = () =>
      wrapper
        .findAll('button.heat-cell[tabindex="0"]')
        .map((b) => b.attributes("data-day"));
    expect(tabbable()).toEqual(["2026-10-16"]);
    await wrapper
      .find('[data-day="2026-10-16"]')
      .trigger("keydown", { key: "ArrowLeft" });
    expect(tabbable()).toEqual(["2026-10-09"]);
    await wrapper
      .find('[data-day="2026-10-09"]')
      .trigger("keydown", { key: "ArrowDown" });
    expect(tabbable()).toEqual(["2026-10-10"]);
    await wrapper
      .find('[data-day="2026-10-10"]')
      .trigger("keydown", { key: "Home" });
    expect(tabbable()).toEqual(["2025-10-12"]);
    // never past either end of the window
    await wrapper
      .find('[data-day="2025-10-12"]')
      .trigger("keydown", { key: "ArrowUp" });
    expect(tabbable()).toEqual(["2025-10-12"]);
  });
});

describe("activity day dialog", () => {
  it("opens from a square, requests the same zone and day, and links entries", async () => {
    respond(day());
    const wrapper = mountHeatmap();
    await wrapper.find('[data-day="2026-10-14"]').trigger("click");
    await flushPromises();
    const url = new URL(fetchMock.mock.calls[0]![0], "http://test");
    expect(url.pathname).toBe("/api/media-stats/activity/day");
    expect(url.searchParams.get("date")).toBe("2026-10-14");
    expect(url.searchParams.get("tz")).toBe("Pacific/Kiritimati");
    const dialog = wrapper.find('[role="dialog"]');
    expect(dialog.text()).toContain("3 episodes, 1 achievement");
    const links = dialog.findAll("a").map((a) => a.attributes("href"));
    expect(links).toEqual(["/tv/show-1", "/games/game-1/achievements/ach-1"]);
  });

  it("closes from its Close button and from a link, leaving the grid usable", async () => {
    respond(day());
    const wrapper = mountHeatmap();
    await wrapper.find('[data-day="2026-10-14"]').trigger("click");
    await flushPromises();
    await wrapper
      .findAll("button")
      .find((b) => b.text() === "Close")!
      .trigger("click");
    expect(wrapper.find('[role="dialog"]').exists()).toBe(false);
    respond(day());
    await wrapper.find('[data-day="2026-10-14"]').trigger("click");
    await flushPromises();
    await wrapper.find('[role="dialog"] a').trigger("click");
    expect(wrapper.find('[role="dialog"]').exists()).toBe(false);
  });

  it("says so when nothing was recorded and does not invent entries", async () => {
    respond(
      day({ count: 0, episodes: 0, achievements: 0, total: 0, items: [] }),
    );
    const wrapper = mountHeatmap();
    await wrapper.find('[data-day="2026-10-13"]').trigger("click");
    await flushPromises();
    expect(wrapper.find('[role="dialog"]').text()).toContain(
      "Nothing was recorded on this day.",
    );
    expect(wrapper.find('[role="dialog"] a').exists()).toBe(false);
  });

  it("pages further entries on request and reports a failed load", async () => {
    respond(day({ total: 3, complete: false, next_offset: 2 }));
    const wrapper = mountHeatmap();
    await wrapper.find('[data-day="2026-10-14"]').trigger("click");
    await flushPromises();
    expect(wrapper.find(".more").text()).toContain("Showing 2 of 3 entries");
    respond(
      day({
        total: 3,
        offset: 2,
        complete: true,
        items: [
          {
            kind: "history",
            media_type: "movie",
            id: "m",
            title: "Movie",
            count: 1,
            event_type: "rated",
            detail: null,
          },
        ],
      }),
    );
    await wrapper.find(".more button").trigger("click");
    await flushPromises();
    expect(
      new URL(fetchMock.mock.calls[1]![0], "http://test").searchParams.get(
        "offset",
      ),
    ).toBe("2");
    expect(wrapper.findAll('[role="dialog"] a')).toHaveLength(3);
    expect(wrapper.find(".more").exists()).toBe(false);

    fetchMock.mockResolvedValueOnce(new Response(null, { status: 503 }));
    await wrapper
      .findAll("button")
      .find((b) => b.text() === "Close")!
      .trigger("click");
    await wrapper.find('[data-day="2026-10-15"]').trigger("click");
    await flushPromises();
    expect(wrapper.find('[role="alert"]').text()).toContain("503");
  });
});

describe("activity helpers", () => {
  it("does calendar arithmetic without the browser zone", () => {
    expect(addDays("2026-03-01", -1)).toBe("2026-02-28");
    expect(addDays("2026-12-31", 1)).toBe("2027-01-01");
    expect(describeCounts({ episodes: 1, achievements: 2 })).toBe(
      "1 episode, 2 achievements",
    );
    expect(describeCounts({})).toBe("nothing");
  });
  it("labels the first column and each month's first week, never overlapping", () => {
    const starts = Array.from({ length: 53 }, (_, i) =>
      addDays("2025-10-12", i * 7),
    );
    const marks = monthMarks(starts);
    // a first partial month too close to the next label is dropped, not overlapped
    expect(marks.map((m) => m.label).slice(0, 3)).toEqual([
      "Nov",
      "Dec",
      "Jan",
    ]);
    expect(new Set(marks.map((m) => m.week)).size).toBe(marks.length);
    for (let i = 1; i < marks.length; i++)
      expect(marks[i]!.week - marks[i - 1]!.week).toBeGreaterThanOrEqual(3);
  });
  it("links each kind to its own detail page", () => {
    expect(
      itemPath({
        kind: "episodes",
        media_type: "anime",
        id: "a",
        title: "",
        count: 1,
      }),
    ).toBe("/anime/a");
    expect(
      itemPath({
        kind: "history",
        media_type: "movie",
        id: "m",
        title: "",
        count: 1,
        event_type: "rated",
        detail: null,
      }),
    ).toBe("/movies/m");
    expect(
      itemPath({
        kind: "achievement",
        id: "x",
        name: "",
        unlocked_at: 1,
        game_id: "g",
        game_title: "",
      }),
    ).toBe("/games/g/achievements/x");
  });
});
