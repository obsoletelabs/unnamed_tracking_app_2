// @vitest-environment happy-dom
import { flushPromises, mount } from "@vue/test-utils";
import { expect, it, vi } from "vitest";
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
  ...over,
});

vi.mock("../services/identityReview", async (original) => ({
  ...(await original<typeof import("../services/identityReview")>()),
  runIdentityMatching: vi.fn(async () => {}),
  fetchReviewQueue: vi.fn(async () => ({
    has_more: false,
    entries: [
      {
        id: "entry-1",
        kind: "copy",
        confidence: 0.6,
        reason: null,
        evidence: { matched: ["base_title"], conflicts: ["platform"] },
        first: game({
          id: "android",
          source: "manual",
          platform: "Android",
          playtime_seconds: 0,
        }),
        second: game({}),
        differences: [],
      },
    ],
  })),
  mergeReviewEntry: vi.fn(async () => {}),
  keepReviewEntrySeparate: vi.fn(async () => {}),
}));

it("names both copies, keeps the most played as main and groups them", async () => {
  const wrapper = mount(IdentityReviewQueue);
  await flushPromises();
  expect(wrapper.text()).toContain("Another copy of Stardew Valley?");
  expect(wrapper.text()).toContain("Steam · PC (Windows)");
  expect(wrapper.text()).toContain("Android");
  expect(wrapper.text()).toContain("Same game, on a different platform.");
  const chosen = wrapper.findAll('input[type="radio"]');
  expect((chosen[1].element as HTMLInputElement).checked).toBe(true);

  await chosen[0].setValue(true);
  const group = wrapper
    .findAll("button")
    .find((b) => b.text() === "Group as copies")!;
  await group.trigger("click");
  await flushPromises();
  expect(review.mergeReviewEntry).toHaveBeenCalledWith("entry-1", "android");
  expect(wrapper.text()).toContain("Nothing to review.");
  wrapper.unmount();
});
