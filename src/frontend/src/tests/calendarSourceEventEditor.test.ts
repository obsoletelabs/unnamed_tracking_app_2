// @vitest-environment happy-dom
import { flushPromises, mount } from "@vue/test-utils";
import { defineComponent, h } from "vue";
import { beforeEach, expect, it, vi } from "vitest";
import type { ExternalCalendarEvent } from "../services/calendarSources";
const api = vi.hoisted(() => ({
  save: vi.fn(),
  clear: vi.fn(),
  copy: vi.fn(),
}));
vi.mock("../services/calendarSources", async (original) => ({
  ...(await original<typeof import("../services/calendarSources")>()),
  setCalendarOverride: api.save,
  clearCalendarOverride: api.clear,
  copyExternalEvent: api.copy,
}));
import CalendarSourceEventEditor from "../components/CalendarSourceEventEditor.vue";
import { createCalendarSource } from "../services/calendarSources";
const event: ExternalCalendarEvent = {
  event_id: "e1",
  uid: "u1",
  recurrence_key: "U:2026-10-19T12:00:00+00:00",
  title: "Watch party",
  note: "Original notes",
  location: "Living room",
  url: "https://example.test/event",
  start: { kind: "utc", value: "2026-10-19T12:00:00+00:00" },
  original_start: { kind: "utc", value: "2026-10-19T12:00:00+00:00" },
  duration_seconds: 3600,
  event_date: "2026-10-19",
  event_time: "12:00",
  end_date: "2026-10-19",
  end_time: "13:00",
  last_date: "2026-10-19",
  recurring: true,
  series_override: true,
  occurrence_override: false,
  source: {
    id: "s1",
    name: "Shared calendar",
    color: "#123456",
    hostname: "calendar.example",
    enabled: true,
    refresh_minutes: 360,
    last_attempt_at: 1,
    last_success_at: 1,
    next_refresh_at: 1000,
    last_status: "updated",
    warnings: [],
    consecutive_failures: 0,
  },
};
const Modal = defineComponent({
  props: ["dismissible"],
  setup(_props, { slots }) {
    return () => h("div", slots.default?.());
  },
});
beforeEach(() => {
  vi.resetAllMocks();
  api.save.mockResolvedValue({ saved: true });
  api.clear.mockResolvedValue({ cleared: true });
});
function open() {
  return mount(CalendarSourceEventEditor, {
    props: { event },
    global: { stubs: { UiModal: Modal } },
  });
}
it("sends only changed fields and an explicit series scope, with duplicate-submit protection", async () => {
  let finish!: () => void;
  api.save.mockReturnValueOnce(
    new Promise<void>((resolve) => {
      finish = resolve;
    }),
  );
  const wrapper = open();
  await wrapper.get("select").setValue("series");
  await wrapper.get('input[maxlength="200"]').setValue("Series title");
  await wrapper.get("form").trigger("submit");
  await wrapper.get("form").trigger("submit");
  expect(api.save).toHaveBeenCalledTimes(1);
  expect(api.save.mock.calls[0]).toEqual([
    "s1",
    expect.objectContaining({
      scope: "series",
      fields: { title: "Series title" },
      recurrence_key: event.recurrence_key,
    }),
  ]);
  finish();
  await flushPromises();
  expect(wrapper.emitted("changed")).toHaveLength(1);
  wrapper.unmount();
});
it("preserves a failed draft and offers scope-specific reset without rewriting the feed", async () => {
  api.save.mockRejectedValueOnce(new Error("Unavailable"));
  const wrapper = open();
  await wrapper.get("textarea").setValue("Keep this draft");
  await wrapper.get("form").trigger("submit");
  await flushPromises();
  expect(wrapper.get("[role=alert]").text()).toBe("Unavailable");
  expect(wrapper.get("textarea").element.value).toBe("Keep this draft");
  expect(wrapper.emitted("close")).toBeUndefined();
  await wrapper.get("select").setValue("series");
  await wrapper
    .findAll("button")
    .find((button) => button.text().includes("Reset series"))!
    .trigger("click");
  await flushPromises();
  expect(api.clear).toHaveBeenCalledWith(
    "s1",
    expect.objectContaining({ scope: "series" }),
  );
  wrapper.unmount();
});
it("does not echo a secret URL from a backend validation error", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => ({
      ok: false,
      status: 422,
      json: async () => ({
        detail: [
          { input: "https://example.test/secret-token", msg: "invalid" },
        ],
      }),
    })),
  );
  await expect(
    createCalendarSource({
      name: "Test",
      url: "https://example.test/secret-token",
      color: "#123456",
      enabled: true,
      refresh_minutes: 360,
    }),
  ).rejects.toThrow("Calendar request failed (422).");
  vi.unstubAllGlobals();
});
