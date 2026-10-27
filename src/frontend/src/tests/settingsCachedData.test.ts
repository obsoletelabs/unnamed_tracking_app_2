// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { flushPromises, shallowMount } from "@vue/test-utils";
import GamePageSection from "../components/settings/GamePageSection.vue";
import AppearanceSection from "../components/settings/AppearanceSection.vue";
import { preferencesLoaded, preferencesError } from "../state/preferences";
import { appearanceSettings } from "../state/appearance";
import { currentUser } from "../state/auth";
import type { CurrentUser } from "../services/auth";
import { fetchPreferences } from "../services/preferences";
import {
  fetchAppearanceSettings,
  updateAppearanceSettings,
} from "../services/appearanceSettings";
import type { AppearanceSettings } from "../services/appearanceSettings";

vi.mock("../services/preferences", async (original) => ({
  ...(await original<typeof import("../services/preferences")>()),
  fetchPreferences: vi.fn(),
}));
vi.mock("../services/appearanceSettings", async (original) => ({
  ...(await original<typeof import("../services/appearanceSettings")>()),
  fetchAppearanceSettings: vi.fn(),
  updateAppearanceSettings: vi.fn(),
}));

const appearance: AppearanceSettings = {
  id: "a",
  user_id: "member",
  completion_badge_style: "border",
  completion_badge_color: "#123456",
  completion_badge_placement: "top-right",
  completion_badge_image_url: null,
  created_at: 0,
  updated_at: 0,
};
beforeEach(() => {
  vi.clearAllMocks();
  currentUser.value = { id: "member" } as CurrentUser;
  preferencesLoaded.value = true;
  preferencesError.value = null;
  appearanceSettings.value = { ...appearance };
});
afterEach(() => {
  currentUser.value = null;
});

it("shows already loaded game-page preferences without another request", async () => {
  const wrapper = shallowMount(GamePageSection);
  await flushPromises();
  expect(wrapper.findComponent({ name: "PageSettingsEditor" }).exists()).toBe(
    true,
  );
  expect(fetchPreferences).not.toHaveBeenCalled();
  wrapper.unmount();
});

it("shows cached appearance and uses the save response without a follow-up GET", async () => {
  const saved = { ...appearance, completion_badge_color: "#abcdef" };
  vi.mocked(updateAppearanceSettings).mockResolvedValue(saved);
  const wrapper = shallowMount(AppearanceSection);
  expect(
    wrapper.find('input[aria-label="Badge hex color"]').element,
  ).toHaveProperty("value", "#123456");
  await wrapper.find('input[aria-label="Badge hex color"]').setValue("#abcdef");
  await wrapper
    .findAll("button")
    .find((button) => button.text() === "Save")!
    .trigger("click");
  await flushPromises();
  expect(fetchAppearanceSettings).not.toHaveBeenCalled();
  expect(appearanceSettings.value?.completion_badge_color).toBe("#abcdef");
  wrapper.unmount();
});
