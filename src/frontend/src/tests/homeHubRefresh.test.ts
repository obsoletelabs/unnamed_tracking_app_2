// @vitest-environment happy-dom
import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import HomeHub from "../views/HomeHub.vue";
import { preferences, preferencesLoaded } from "../state/preferences";
import { currentUser } from "../state/auth";
import { DEFAULT_PREFERENCES } from "../services/preferences";
import type { Game } from "../types/game";

const mocks = vi.hoisted(() => ({
  fetchGames: vi.fn(),
  fetchWeeklyDigest: vi.fn(),
}));
vi.mock("../services/games", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../services/games")>()),
  fetchGames: (...args: unknown[]) => mocks.fetchGames(...args),
  gameLibraryRevision: () => 0,
}));
vi.mock("../services/stats", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../services/stats")>()),
  fetchWeeklyDigest: (...args: unknown[]) => mocks.fetchWeeklyDigest(...args),
}));

const game = (id: string): Game =>
  ({
    id,
    title: id,
    relationshipType: null,
    collections: [],
  }) as unknown as Game;
const user = (id: string) =>
  ({ id, username: `user-${id}`, display_name: null }) as never;
const wrappers: ReturnType<typeof mount>[] = [];

function home(widgets = ["library-summary", "weekly-digest"]) {
  preferences.value = { ...DEFAULT_PREFERENCES, home_widgets: widgets };
  preferencesLoaded.value = true;
  currentUser.value = user("one");
  const wrapper = mount(HomeHub, {
    global: {
      stubs: {
        AccountChip: true,
        RouterLink: true,
        HomeCoreWidget: {
          props: ["widgetId", "games"],
          template:
            '<div data-testid="core-data">{{ widgetId }} {{ games.length }}</div>',
        },
      },
    },
  });
  wrappers.push(wrapper);
  return wrapper;
}

beforeEach(() => {
  vi.clearAllMocks();
  mocks.fetchGames.mockResolvedValue([game("first")]);
  mocks.fetchWeeklyDigest.mockResolvedValue({ days: [] });
  preferences.value = { ...DEFAULT_PREFERENCES };
  preferencesLoaded.value = false;
  currentUser.value = null;
});
afterEach(() => {
  wrappers.splice(0).forEach((wrapper) => wrapper.unmount());
});

it("refreshes only selected built-in data and shows its successful update times", async () => {
  const wrapper = home();
  await flushPromises();
  mocks.fetchGames.mockClear();
  mocks.fetchWeeklyDigest.mockClear();

  await wrapper.get('button[aria-label="Refresh Home"]').trigger("click");
  await flushPromises();

  expect(mocks.fetchGames).toHaveBeenCalledTimes(1);
  expect(mocks.fetchWeeklyDigest).toHaveBeenCalledTimes(1);
  expect(wrapper.get('[role="status"]').text()).toContain(
    "Home data refreshed successfully.",
  );
  expect(wrapper.get(".home-updated-at").text()).toContain("Games");
  expect(wrapper.get(".home-updated-at").text()).toContain("This week");

  wrapper.unmount();
  const selectedGamesOnly = home(["library-summary"]);
  await flushPromises();
  mocks.fetchGames.mockClear();
  mocks.fetchWeeklyDigest.mockClear();
  await selectedGamesOnly
    .get('button[aria-label="Refresh Home"]')
    .trigger("click");
  await flushPromises();
  expect(mocks.fetchGames).toHaveBeenCalledTimes(1);
  expect(mocks.fetchWeeklyDigest).not.toHaveBeenCalled();
});

it("reports partial failures, keeps old content, and preserves the failed source timestamp", async () => {
  const wrapper = home();
  await flushPromises();
  const previousUpdate = wrapper.get(".home-updated-at").text();
  mocks.fetchGames.mockResolvedValueOnce([game("second")]);
  mocks.fetchWeeklyDigest.mockRejectedValueOnce(new Error("Activity offline"));

  await wrapper.get('button[aria-label="Refresh Home"]').trigger("click");
  await flushPromises();

  expect(wrapper.get('[role="status"]').text()).toContain(
    "Updated games. Could not update this week's activity",
  );
  expect(
    wrapper
      .get('[data-widget-id="library-summary"] [data-testid="core-data"]')
      .text(),
  ).toBe("library-summary 1");
  expect(
    wrapper.get('[data-widget-id="weekly-digest"] [role="alert"]').text(),
  ).toContain("Activity offline");
  expect(wrapper.get(".home-updated-at").text()).toBe(previousUpdate);
});

it("keeps content and last-successful timestamps after every selected refresh fails", async () => {
  const wrapper = home();
  await flushPromises();
  const previousUpdate = wrapper.get(".home-updated-at").text();
  mocks.fetchGames.mockRejectedValueOnce(new Error("Games offline"));
  mocks.fetchWeeklyDigest.mockRejectedValueOnce(new Error("Activity offline"));

  await wrapper.get('button[aria-label="Refresh Home"]').trigger("click");
  await flushPromises();

  expect(wrapper.get('[role="status"]').text()).toContain(
    "Could not refresh games",
  );
  expect(
    wrapper
      .get('[data-widget-id="library-summary"] [data-testid="core-data"]')
      .text(),
  ).toBe("library-summary 1");
  expect(wrapper.get(".home-updated-at").text()).toBe(previousUpdate);
});

it("blocks overlapping refreshes", async () => {
  const wrapper = home(["library-summary"]);
  await flushPromises();
  let finish!: (games: Game[]) => void;
  mocks.fetchGames.mockImplementationOnce(
    () => new Promise<Game[]>((resolve) => (finish = resolve)),
  );
  mocks.fetchGames.mockClear();

  const button = wrapper.get('button[aria-label="Refresh Home"]');
  await button.trigger("click");
  await button.trigger("click");
  expect(mocks.fetchGames).toHaveBeenCalledTimes(1);
  expect(button.attributes("disabled")).toBeDefined();
  finish([game("latest")]);
  await flushPromises();
  expect(wrapper.get('[role="status"]').text()).toContain(
    "Home data refreshed successfully.",
  );
});

it("ignores a refresh response from the previous account", async () => {
  const wrapper = home(["library-summary"]);
  await flushPromises();
  let finish!: (games: Game[]) => void;
  mocks.fetchGames.mockImplementationOnce(
    () => new Promise<Game[]>((resolve) => (finish = resolve)),
  );

  await wrapper.get('button[aria-label="Refresh Home"]').trigger("click");
  currentUser.value = user("two");
  await flushPromises();
  finish([game("old-account")]);
  await flushPromises();

  expect(wrapper.text()).not.toContain("Home data refreshed successfully.");
  expect(wrapper.get(".home-updated-at").text()).toContain(
    "has not loaded yet",
  );
});
