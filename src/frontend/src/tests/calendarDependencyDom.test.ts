// @vitest-environment happy-dom
import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, expect, it, vi } from "vitest";
import CalendarSettingsSection from "../components/settings/CalendarSettingsSection.vue";
import { DEFAULT_PREFERENCES } from "../services/preferences";

const store = vi.hoisted(() => ({ value: {} as Record<string, unknown> }));
vi.mock("../services/preferences", async (importOriginal) => {
  const actual =
    await importOriginal<typeof import("../services/preferences")>();
  return {
    ...actual,
    fetchPreferences: vi.fn(async () => ({ ...store.value })),
    queuePreferences: vi.fn(async (changes: Record<string, unknown>) => {
      store.value = { ...store.value, ...changes };
      return { prefs: { ...store.value }, latest: true };
    }),
  };
});

beforeEach(() => {
  store.value = {
    ...DEFAULT_PREFERENCES,
    calendar_game_releases: true,
    calendar_game_history: true,
    calendar_hide_games: true,
  };
});

function toggle(wrapper: ReturnType<typeof mount>, text: string) {
  const found = wrapper
    .findAll("button[role=switch]")
    .find((b) => b.text().includes(text));
  if (!found) throw new Error(`no switch ${text}`);
  return found;
}

it("disables game layers with a described reason and restores them", async () => {
  const wrapper = mount(CalendarSettingsSection);
  await flushPromises();
  const releases = toggle(wrapper, "Game releases");
  expect(releases.attributes("disabled")).toBeDefined();
  expect(releases.attributes("aria-checked")).toBe("true");
  const id = releases.attributes("aria-describedby");
  expect(id).toBeTruthy();
  const reason = wrapper.find(`#${id}`);
  expect(reason.attributes("role")).toBe("status");
  expect(reason.text()).toContain("Hide all games layers");
  expect(reason.text()).toContain("Your choice is kept.");

  await toggle(wrapper, "Hide all games layers").trigger("click");
  await flushPromises();
  const after = toggle(wrapper, "Game releases");
  expect(after.attributes("disabled")).toBeUndefined();
  expect(after.attributes("aria-describedby")).toBeUndefined();
  expect(after.attributes("aria-checked")).toBe("true");
  expect(toggle(wrapper, "Games history").attributes("aria-checked")).toBe(
    "true",
  );
  expect(store.value.calendar_game_releases).toBe(true);
  expect(wrapper.find("#calendar-games-reason").exists()).toBe(false);
});
