// @vitest-environment happy-dom
import { flushPromises, mount } from "@vue/test-utils";
import { expect, it, vi } from "vitest";
import GameCard from "../components/GameCard.vue";
import GameVersionDisplaySection from "../components/settings/GameVersionDisplaySection.vue";
import { mockGames } from "../data/mockGames";
import { DEFAULT_PREFERENCES } from "../services/preferences";

const push = vi.hoisted(() => vi.fn());
vi.mock("vue-router", () => ({ useRouter: () => ({ push }) }));
const prefs = vi.hoisted(() => ({
  queuePreferences: vi.fn(),
  fetchPreferences: vi.fn(),
}));
vi.mock("../services/preferences", async (original) => ({
  ...(await original<typeof import("../services/preferences")>()),
  fetchPreferences: prefs.fetchPreferences,
  queuePreferences: prefs.queuePreferences,
}));

it("names the copies, the named version and where the card opens", async () => {
  const game = { ...mockGames[0]!, id: "java" };
  const global = { stubs: { Teleport: true, RouterLink: true } };
  const plain = mount(GameCard, {
    props: { game, copySummary: "3 copies · Steam, Xbox, Nintendo Switch" },
    global,
  });
  expect(plain.get('[data-testid="version-count"]').text()).toBe(
    "3 copies · Steam, Xbox, Nintendo Switch",
  );
  const named = mount(GameCard, {
    props: {
      game,
      copySummary: "3 copies · Steam, Xbox, Nintendo Switch",
      versionContext: { label: "Java Edition", gameId: "java-2" },
    },
    global,
  });
  expect(named.find('[data-testid="version-count"]').exists()).toBe(false);
  expect(named.get('[data-testid="version-context"]').text()).toBe(
    "Java Edition",
  );
  await named.get(".cover").trigger("click");
  expect(push).toHaveBeenCalledWith("/games/java-2");
  const single = mount(GameCard, {
    props: { game, copySummary: null },
    global,
  });
  expect(single.find('[data-testid="version-count"]').exists()).toBe(false);
});

it("saves the version display and relationship rules, then recovers from a failure", async () => {
  prefs.fetchPreferences.mockResolvedValue({ ...DEFAULT_PREFERENCES });
  prefs.queuePreferences.mockImplementation(async (changes) => ({
    prefs: { ...DEFAULT_PREFERENCES, ...changes },
    latest: true,
  }));
  const wrapper = mount(GameVersionDisplaySection);
  await flushPromises();
  const separate = wrapper
    .findAll("button")
    .find((b) => b.text() === "Every version on its own")!;
  await separate.trigger("click");
  await flushPromises();
  expect(prefs.queuePreferences).toHaveBeenCalledWith({
    library_version_display: "separate",
  });
  const dlc = wrapper.findAll("select")[0]!;
  await dlc.setValue("nest");
  await flushPromises();
  expect(prefs.queuePreferences).toHaveBeenLastCalledWith({
    library_relationship_display: { dlc_of: "nest" },
  });
  prefs.queuePreferences.mockRejectedValueOnce(new Error("Offline"));
  await dlc.setValue("hidden");
  await flushPromises();
  expect(wrapper.get('[role="alert"]').text()).toContain("Offline");
});
