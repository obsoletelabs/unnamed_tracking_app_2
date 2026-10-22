// @vitest-environment happy-dom
import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, describe, expect, it, vi } from "vitest";
import LibraryManagementSection from "../components/settings/LibraryManagementSection.vue";
import type { Game } from "../types/game";

const mocks = vi.hoisted(() => ({
  fetchGames: vi.fn(),
  refreshGameAchievements: vi.fn(),
  refreshGameMetadata: vi.fn(),
  previewGameMetadataRefresh: vi.fn(),
  fetchGameTrash: vi.fn(),
  restoreGame: vi.fn(),
}));

vi.mock("../services/games", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../services/games")>()),
  ...mocks,
}));

vi.mock("../state/taskProgress", () => ({
  startTask: vi.fn(() => "task-1"),
  updateTask: vi.fn(),
  completeTask: vi.fn(),
  errorTask: vi.fn(),
  addFeedItem: vi.fn(),
}));

afterEach(() => vi.clearAllMocks());

describe("library achievement import", () => {
  it("checks games with no saved achievements so new catalogues can be imported", async () => {
    const library = [
      { id: "new-game", title: "Portal 2" },
      { id: "existing-game", title: "Hades" },
    ] as Game[];
    mocks.fetchGames.mockResolvedValue(library);
    mocks.fetchGameTrash.mockResolvedValue([]);
    mocks.refreshGameAchievements.mockResolvedValue({
      provider: "Atlas",
      achievements: 1,
      unlocked: 0,
      hidden: 0,
    });

    const wrapper = mount(LibraryManagementSection);
    await flushPromises();
    const button = wrapper
      .findAll("button")
      .find((item) => item.text() === "Import / refresh achievements");
    expect(button).toBeDefined();

    await button!.trigger("click");
    await flushPromises();

    expect(mocks.refreshGameAchievements).toHaveBeenCalledTimes(2);
    expect(mocks.refreshGameAchievements).toHaveBeenCalledWith("new-game");
    expect(mocks.refreshGameAchievements).toHaveBeenCalledWith("existing-game");
    expect(wrapper.text()).toContain("2 games checked: 2 achievements");
    wrapper.unmount();
  });
});
