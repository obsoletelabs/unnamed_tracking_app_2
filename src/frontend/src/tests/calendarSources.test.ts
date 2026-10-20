// @vitest-environment happy-dom
import { flushPromises, mount } from "@vue/test-utils";
import { defineComponent, h, ref } from "vue";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type {
  CalendarSource,
  ExternalCalendarEvent,
} from "../services/calendarSources";

const api = vi.hoisted(() => ({ sources: vi.fn(), events: vi.fn() }));
vi.mock("../services/calendarSources", () => ({
  fetchCalendarSources: api.sources,
  fetchSourceEvents: api.events,
}));
import { useCalendarSources } from "../composables/useCalendarSources";
import { currentUser } from "../state/auth";

const user = (id: string) => ({
  id,
  username: id,
  email: `${id}@example.test`,
  is_admin: false,
  steamgriddb_api_key: null,
});
const source: CalendarSource = {
  id: "source",
  name: "Watch parties",
  color: "#123456",
  hostname: "calendar.example",
  enabled: true,
  refresh_minutes: 360,
  last_attempt_at: 1,
  last_success_at: 1,
  next_refresh_at: 999,
  last_status: "updated",
  consecutive_failures: 0,
  warnings: [],
};
const event = (
  title: string,
  day = "2026-10-19",
): Omit<ExternalCalendarEvent, "source"> => ({
  event_id: "event",
  uid: title,
  recurrence_key: "",
  title,
  note: "",
  location: "",
  url: "",
  start: { kind: "date", value: day },
  original_start: { kind: "date", value: day },
  duration_seconds: 86400,
  event_date: day,
  event_time: null,
  end_date: day,
  end_time: null,
  last_date: day,
  recurring: false,
  series_override: false,
  occurrence_override: false,
});
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}
const mounted: (() => void)[] = [];
beforeEach(() => {
  currentUser.value = user("first");
  api.sources.mockResolvedValue({ remote_enabled: true, sources: [source] });
  api.events.mockResolvedValue({ events: [event("October")], warnings: [] });
});
afterEach(() => {
  mounted.splice(0).forEach((close) => close());
  vi.clearAllMocks();
  currentUser.value = null;
});
async function open() {
  const range = ref({ start: "2026-10-01", end: "2026-10-31" });
  let state!: ReturnType<typeof useCalendarSources>;
  const wrapper = mount(
    defineComponent({
      setup() {
        state = useCalendarSources(range);
        return () => h("div");
      },
    }),
  );
  mounted.push(() => wrapper.unmount());
  await state.load();
  return { state, range };
}

it("retains earlier months and useful data on failure, with a retry that clears the error", async () => {
  const { state, range } = await open();
  api.events.mockResolvedValueOnce({
    events: [event("November", "2026-11-19")],
    warnings: [],
  });
  range.value = { start: "2026-11-01", end: "2026-11-30" };
  await flushPromises();
  expect(state.events.value.map((item) => item.title)).toEqual([
    "October",
    "November",
  ]);
  api.events.mockRejectedValueOnce(new Error("offline"));
  range.value = { start: "2026-10-01", end: "2026-10-31" };
  await flushPromises();
  expect(state.error.value).toContain("Previously loaded");
  expect(state.events.value).toHaveLength(2);
  await state.load();
  expect(state.error.value).toBe("");
});

it("discards an older response after navigating to a newer window", async () => {
  const { state, range } = await open();
  const old = deferred<{
    events: Omit<ExternalCalendarEvent, "source">[];
    warnings: string[];
  }>();
  api.events.mockReturnValueOnce(old.promise);
  range.value = { start: "2026-11-01", end: "2026-11-30" };
  await flushPromises();
  api.events.mockResolvedValueOnce({
    events: [event("Newest", "2026-12-19")],
    warnings: [],
  });
  range.value = { start: "2026-12-01", end: "2026-12-31" };
  await flushPromises();
  old.resolve({ events: [event("Stale", "2026-11-19")], warnings: [] });
  await flushPromises();
  expect(state.events.value.map((item) => item.title)).toEqual([
    "October",
    "Newest",
  ]);
  expect(state.loading.value).toBe(false);
});

it("clears all cached data on account change and ignores the previous account's response", async () => {
  const { state } = await open();
  const old = deferred<{
    remote_enabled: boolean;
    sources: CalendarSource[];
  }>();
  api.sources.mockReturnValueOnce(old.promise);
  const pending = state.load();
  api.sources.mockResolvedValueOnce({ remote_enabled: true, sources: [] });
  currentUser.value = user("second");
  await flushPromises();
  expect(state.events.value).toEqual([]);
  old.resolve({ remote_enabled: true, sources: [source] });
  await pending;
  expect(state.events.value).toEqual([]);
});

it("removes deleted or disabled sources, while showing snapshot and expansion warnings", async () => {
  api.events.mockResolvedValueOnce({
    events: [event("October")],
    warnings: ["unsupported_recurrence"],
  });
  const { state } = await open();
  expect(state.notices.value).toEqual(["unsupported_recurrence"]);
  api.sources.mockResolvedValueOnce({
    remote_enabled: false,
    sources: [{ ...source, enabled: false }],
  });
  await state.load();
  expect(state.events.value).toEqual([]);
  expect(state.error.value).toBe("");
});

it("reconciles an empty successful window without retaining cancelled events", async () => {
  const { state } = await open();
  api.events.mockResolvedValueOnce({ events: [], warnings: [] });
  await state.load();
  expect(state.events.value).toEqual([]);
  expect(state.error.value).toBe("");
});
