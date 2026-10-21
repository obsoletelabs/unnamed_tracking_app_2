// @vitest-environment happy-dom
import { flushPromises, mount } from "@vue/test-utils";
import { expect, it, vi } from "vitest";
import GameManualTrackingPanel from "../components/game/GameManualTrackingPanel.vue";

const state = vi.hoisted(() => ({ statuses: ["PLAYING", "PLAYING"] }));
vi.mock("../state/dialog", () => ({
  useConfirm: () => vi.fn(async () => true),
}));
vi.mock("../services/gameTracking", () => ({
  fetchCompletion: vi.fn(async () => ({
    completed: 0,
    of: state.statuses.length,
    partial: false,
    allCompleted: false,
    summary: "Not completed",
    versions: state.statuses.map((status, i) => ({
      gameId: `g${i}`,
      title: "Game",
      platform: "PC",
      source: i ? "GOG" : "Steam",
      status,
      completed: false,
      userSet: false,
    })),
  })),
}));

const stubs = { RouterLink: { template: "<a><slot /></a>" } };
async function panel() {
  const wrapper = mount(GameManualTrackingPanel, {
    props: { gameId: "g0", part: "completion" },
    global: { stubs },
  });
  await flushPromises();
  return wrapper;
}
const hidden = (w: Awaited<ReturnType<typeof panel>>) =>
  (w.get("#completion-versions").attributes("style") ?? "").includes("none");

it("keeps the per-copy statuses one click away when the copies agree", async () => {
  state.statuses = ["PLAYING", "PLAYING"];
  const wrapper = await panel();
  expect(hidden(wrapper)).toBe(true);
  const toggle = wrapper.get("button[aria-controls='completion-versions']");
  expect(toggle.attributes("aria-expanded")).toBe("false");
  await toggle.trigger("click");
  expect(hidden(wrapper)).toBe(false);
  expect(toggle.attributes("aria-expanded")).toBe("true");
});

it("shows them straight away when the copies disagree", async () => {
  state.statuses = ["PLAYING", "BEATEN"];
  const wrapper = await panel();
  expect(hidden(wrapper)).toBe(false);
});
