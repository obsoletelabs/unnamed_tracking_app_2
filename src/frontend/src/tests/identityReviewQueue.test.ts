// @vitest-environment happy-dom
import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, expect, it, vi } from "vitest";
import IdentityReviewQueue from "../components/settings/IdentityReviewQueue.vue";
import * as review from "../services/identityReview";

const game = (over: Partial<review.ReviewGame>): review.ReviewGame => ({
  id: "steam",
  title: "Stardew Valley",
  source: "Steam",
  platform: "PC (Windows)",
  release_date: null,
  external_id: null,
  provider_ids: {},
  status: "PLAYING",
  playtime_seconds: 140 * 3600,
  last_played_at: null,
  reported_by: [
    { kind: "host", source: "steam", label: "Steam", disconnected: false },
  ],
  ...over,
});

const queue = vi.hoisted(() => ({ entries: [] as review.ReviewEntry[] }));
vi.mock("../services/identityReview", async (original) => ({
  ...(await original<typeof import("../services/identityReview")>()),
  runIdentityMatching: vi.fn(async () => {}),
  fetchReviewQueue: vi.fn(async () => ({
    has_more: false,
    entries: queue.entries,
  })),
  mergeReviewEntry: vi.fn(async () => {}),
  relateReviewEntry: vi.fn(async () => {}),
  keepReviewEntrySeparate: vi.fn(async () => {}),
}));

const RouterLinkStub = { template: "<a><slot /></a>" };
const mountQueue = () =>
  mount(IdentityReviewQueue, {
    global: { stubs: { RouterLink: RouterLinkStub } },
  });
const button = (wrapper: ReturnType<typeof mountQueue>, text: string) =>
  wrapper.findAll("button").find((b) => b.text() === text)!;

beforeEach(() => {
  vi.clearAllMocks();
  queue.entries = [
    {
      id: "entry-1",
      kind: "copy",
      confidence: 0.6,
      reason: null,
      evidence: { matched: ["base_title"], conflicts: ["platform", "store"] },
      first: game({
        id: "android",
        source: "Epic",
        platform: "Android",
        playtime_seconds: 0,
        reported_by: [
          {
            kind: "host",
            source: "playnite",
            label: "Playnite",
            disconnected: false,
          },
        ],
      }),
      second: game({}),
      differences: [],
    },
  ];
});

it("says where each record came from, including Playnite", async () => {
  const wrapper = mountQueue();
  await flushPromises();
  expect(wrapper.text()).toContain("Another copy of Stardew Valley?");
  expect(wrapper.text()).toContain("via Playnite");
  expect(wrapper.text()).toContain("via Steam sync");
  expect(wrapper.text()).toContain("Epic · Android, from Playnite");
  wrapper.unmount();
});

it("keeps the most played copy as main and groups the pair as one game", async () => {
  const wrapper = mountQueue();
  await flushPromises();
  const mains = wrapper.findAll('fieldset.main-pick input[type="radio"]');
  expect((mains[1].element as HTMLInputElement).checked).toBe(true);

  await mains[0].setValue(true);
  await wrapper.find("select").setValue("link");
  await button(wrapper, "Group as one game").trigger("click");
  await flushPromises();
  expect(review.mergeReviewEntry).toHaveBeenCalledWith(
    "entry-1",
    "android",
    "link",
    false,
  );
  expect(wrapper.text()).toContain("Nothing to review.");
  wrapper.unmount();
});

it("can record a remake, port or DLC instead of grouping", async () => {
  const wrapper = mountQueue();
  await flushPromises();
  await wrapper.find('input[value="related"]').setValue(true);
  const [from, kind] = wrapper.findAll("select");
  await from.setValue("steam");
  await kind.setValue("port_of");
  await button(wrapper, "Save relationship").trigger("click");
  await flushPromises();
  expect(review.relateReviewEntry).toHaveBeenCalledWith("entry-1", {
    action: "related",
    kind: "port_of",
    fromId: "steam",
  });
  expect(review.mergeReviewEntry).not.toHaveBeenCalled();
  wrapper.unmount();
});

it("keeps unrelated games separate", async () => {
  const wrapper = mountQueue();
  await flushPromises();
  await wrapper.find('input[value="separate"]').setValue(true);
  await button(wrapper, "Keep separate").trigger("click");
  await flushPromises();
  expect(review.keepReviewEntrySeparate).toHaveBeenCalledWith("entry-1");
  wrapper.unmount();
});

it("keeps the entry and shows the error when saving fails", async () => {
  vi.mocked(review.mergeReviewEntry).mockRejectedValueOnce(new Error("Nope"));
  const wrapper = mountQueue();
  await flushPromises();
  await button(wrapper, "Group as one game").trigger("click");
  await flushPromises();
  expect(wrapper.text()).toContain("Nope");
  expect(wrapper.text()).toContain("Another copy of Stardew Valley?");
  wrapper.unmount();
});

const epicPair = (secondSource: string) => {
  queue.entries = [
    {
      id: "entry-2",
      kind: "duplicate",
      confidence: 0.75,
      reason: null,
      evidence: { matched: ["title", "platform"] },
      first: game({ id: "pn", source: "Epic" }),
      second: game({ id: "ep", source: secondSource }),
      differences: [],
    },
  ];
};

it("offers 'same copy' for one store under two names, and sends it", async () => {
  epicPair("Epic Games");
  const wrapper = mountQueue();
  await flushPromises();
  const sameCopy = wrapper.find('input[name$="-copy"]');
  expect((sameCopy.element as HTMLInputElement).checked).toBe(true);
  await button(wrapper, "Group as one game").trigger("click");
  await flushPromises();
  expect(review.mergeReviewEntry).toHaveBeenCalledWith(
    "entry-2",
    expect.any(String),
    "default",
    true,
  );
  wrapper.unmount();
});

it("never offers 'same copy' for Epic against Steam", async () => {
  epicPair("Steam");
  const wrapper = mountQueue();
  await flushPromises();
  const [sameCopy, different] = wrapper.findAll('input[name$="-copy"]');
  expect((sameCopy.element as HTMLInputElement).disabled).toBe(true);
  expect((different.element as HTMLInputElement).checked).toBe(true);
  expect(wrapper.text()).toContain("Epic and Steam are different stores");
  await button(wrapper, "Group as one game").trigger("click");
  await flushPromises();
  expect(review.mergeReviewEntry).toHaveBeenCalledWith(
    "entry-2",
    expect.any(String),
    "default",
    false,
  );
  wrapper.unmount();
});
