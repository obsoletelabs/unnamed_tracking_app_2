// @vitest-environment happy-dom
import { afterEach, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import Notifications from "../views/Notifications.vue";
import {
  expiryOf,
  isActionable,
  lifecycleHistory,
  retentionSummary,
} from "../utils/notificationLifecycle";
import type { InboxRetentionPolicy } from "../services/notifications";

vi.mock("vue-router", () => ({
  useRouter: () => ({ push: vi.fn() }),
  RouterLink: { props: ["to"], template: '<a :href="to"><slot /></a>' },
}));

const policy: InboxRetentionPolicy = {
  default_days: 180,
  maximum_days: 90,
  requested_days: 365,
  effective_days: 90,
  inherited: false,
  limited: true,
};
function raw(id: string, extra: Record<string, unknown> = {}) {
  return {
    id,
    kind: "game_sale",
    media_type: "game",
    media_id: "g1",
    title: `Title ${id}`,
    body: "Body",
    poster_url: null,
    event_at: 1_800_000_000,
    created_at: 1_800_000_000,
    read: false,
    state: "unread",
    source: "store",
    severity: "info",
    ...extra,
  };
}
const calls: string[] = [];
function serve(pages: Record<string, unknown>) {
  calls.length = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      calls.push(`${init?.method ?? "GET"} ${url}`);
      if (url.startsWith("/api/notifications/policy"))
        return new Response(JSON.stringify(policy));
      if (url.startsWith("/api/notifications?")) {
        const query = new URLSearchParams(url.split("?")[1]);
        const key = `${query.get("state") ?? "active"}:${query.get("offset") ?? 0}`;
        return new Response(JSON.stringify(pages[key]));
      }
      return new Response(null, { status: 204 });
    }),
  );
}
const base = {
  unread: 1,
  counts: {},
  sources: ["store"],
  states: { active: 2, dismissed: 1, history: 3 },
};
async function open() {
  const wrapper = mount(Notifications, {
    global: {
      stubs: {
        AppTopBar: { template: "<div><slot /><slot name='actions' /></div>" },
      },
    },
  });
  await flushPromises();
  return wrapper;
}
afterEach(() => vi.unstubAllGlobals());

it("explains requested versus effective retention from the existing policy", async () => {
  serve({ "active:0": { ...base, items: [], total: 0 } });
  const wrapper = await open();
  const note = wrapper.get("[data-testid=retention-note]").text();
  expect(note).toContain("kept for 90 days");
  expect(note).toContain("asked for 365 days");
  expect(calls).toContain("GET /api/notifications/policy");
  expect(
    retentionSummary({ ...policy, limited: false, inherited: true }),
  ).toContain("server default");
});

it("pages with the server offset and keeps lifecycle slices separate", async () => {
  serve({
    "active:0": { ...base, items: [raw("a")], total: 2, next_offset: 1 },
    "active:1": { ...base, items: [raw("b")], total: 2, next_offset: null },
    "dismissed:0": {
      ...base,
      items: [raw("d", { state: "dismissed", dismissed_at: 1_800_000_500 })],
      total: 1,
      next_offset: null,
    },
  });
  const wrapper = await open();
  expect(wrapper.text()).toContain("1 of 2 matching");
  await wrapper
    .findAll("button")
    .find((b) => b.text() === "Load more")!
    .trigger("click");
  await flushPromises();
  expect(calls.some((c) => c.includes("offset=1"))).toBe(true);
  expect(wrapper.text()).toContain("2 of 2 matching");

  await wrapper.findAll("select")[0]!.setValue("dismissed");
  await flushPromises();
  expect(calls.at(-1)).toContain("state=dismissed");
  expect(wrapper.text()).toContain("Title d");
  expect(wrapper.text()).not.toContain("Title a");
});

it("offers read, dismiss and delete only for active items and shows history", async () => {
  serve({
    "active:0": { ...base, items: [raw("a")], total: 1, next_offset: null },
    "dismissed:0": {
      ...base,
      items: [raw("d", { state: "dismissed", dismissed_at: 1_800_000_500 })],
      total: 1,
      next_offset: null,
    },
  });
  const wrapper = await open();
  await wrapper.get(".card-row").trigger("click");
  const labels = () =>
    wrapper.findAll(".detail-actions button").map((b) => b.text());
  expect(labels()).toEqual(
    expect.arrayContaining(["Mark Read", "Dismiss", "Delete"]),
  );
  await wrapper
    .findAll(".detail-actions button")
    .find((b) => b.text() === "Mark Read")!
    .trigger("click");
  await flushPromises();
  expect(calls).toContain("POST /api/notifications/a/read");

  await wrapper.findAll("select")[0]!.setValue("dismissed");
  await flushPromises();
  await wrapper.get(".card-row").trigger("click");
  expect(labels()).toEqual([]);
  expect(wrapper.get(".history").text()).toContain("Dismissed");
  expect(wrapper.text()).toContain("Dismissed");
});

it("derives actionability, expiry and the lifecycle timeline", () => {
  const item = {
    ...({} as object),
    eventAt: 1000,
    createdAt: 1000,
    readAt: 2000,
    dismissedAt: 3000,
    expiresAt: null,
    state: "dismissed",
  } as never;
  expect(isActionable({ state: "dismissed", read: true })).toBe(false);
  expect(isActionable({ state: undefined, read: false })).toBe(true);
  expect(expiryOf(item, policy)).toBe(1000 + 90 * 86400);
  expect(expiryOf(item, { ...policy, effective_days: 0 })).toBeNull();
  expect(lifecycleHistory(item, policy).map((e) => e.label)).toEqual([
    "Received",
    "Read",
    "Dismissed",
    "Expires",
  ]);
});
