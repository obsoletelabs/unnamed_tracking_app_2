// @vitest-environment happy-dom
import { flushPromises, mount, type VueWrapper } from "@vue/test-utils";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import GameCard from "../components/GameCard.vue";
import { mockGames } from "../data/mockGames";
import type { Game } from "../types/game";
import { setFavorite, setStatus } from "../services/games";

vi.mock("../services/games", () => ({
  setFavorite: vi.fn(),
  setStatus: vi.fn(),
}));
vi.mock("vue-router", () => ({ useRouter: () => ({ push: vi.fn() }) }));
const wrappers: VueWrapper[] = [];
beforeEach(() => vi.resetAllMocks());
afterEach(() => wrappers.splice(0).forEach((wrapper) => wrapper.unmount()));
function card(fields: Partial<Game> = {}) {
  const game: Game = {
    ...mockGames[0]!,
    id: "card",
    favorite: false,
    status: "backlog",
    priority: "1",
    ...fields,
  };
  const wrapper = mount(GameCard, {
    props: { game },
    global: { stubs: { Teleport: true, RouterLink: true } },
  });
  wrappers.push(wrapper);
  return { wrapper, game };
}

it("emits the returned favorite record so the library can reconcile canonical state", async () => {
  const { wrapper, game } = card();
  const saved = { ...game, favorite: true, updatedAt: 7 };
  vi.mocked(setFavorite).mockResolvedValue(saved);
  await wrapper.get('button[title="Favorite"]').trigger("click");
  await flushPromises();
  expect(wrapper.emitted("updated")).toEqual([[saved]]);
});

it("emits the saved status and hides inactive priority immediately", async () => {
  const { wrapper, game } = card();
  const saved = {
    ...game,
    status: "beaten" as const,
    completionDate: "2026-10-15",
  };
  vi.mocked(setStatus).mockResolvedValue(saved);
  await wrapper
    .get(".cover")
    .trigger("contextmenu", { clientX: 30, clientY: 30 });
  await wrapper
    .findAll("button")
    .find((button) => button.text() === "Change Status")!
    .trigger("click");
  await wrapper
    .findAll("button")
    .find((button) => button.text() === "beaten")!
    .trigger("click");
  await flushPromises();
  expect(wrapper.emitted("updated")).toEqual([[saved]]);
  expect(wrapper.find('[aria-label="Priority 1"]').exists()).toBe(false);
});

it("rolls back a failed favorite save and exposes an actionable error", async () => {
  const { wrapper } = card();
  vi.mocked(setFavorite).mockRejectedValue(new Error("Offline"));
  await wrapper.get('button[title="Favorite"]').trigger("click");
  await flushPromises();
  expect(wrapper.emitted("updated")).toBeUndefined();
  expect(wrapper.get('button[title="Favorite"]').classes()).not.toContain(
    "active",
  );
  expect(wrapper.get('[role="alert"]').text()).toContain("Offline");
});

it("shows normalized active priority and follows a parent status update", async () => {
  const { wrapper, game } = card({ priority: "HIGH" });
  expect(wrapper.get('[aria-label="Priority 1"]').text()).toContain("1");
  await wrapper.setProps({ game: { ...game, status: "mastered" } });
  expect(wrapper.find('[aria-label="Priority 1"]').exists()).toBe(false);
});
