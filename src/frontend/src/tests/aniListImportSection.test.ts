// @vitest-environment happy-dom
import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, describe, expect, it, vi } from "vitest";
import AniListImportSection from "../components/settings/AniListImportSection.vue";
import { DEFAULT_PREFERENCES } from "../services/preferences";

vi.mock("../services/preferences", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../services/preferences")>()),
  fetchPreferences: vi.fn().mockResolvedValue({
    ...(await importOriginal<typeof import("../services/preferences")>())
      .DEFAULT_PREFERENCES,
    anilist_import_username: "someone",
  }),
  queuePreferences: vi.fn(),
}));

afterEach(() => vi.clearAllMocks());

describe("AniList import settings", () => {
  it("keeps the import choices but points the schedule at Background tasks", async () => {
    const wrapper = mount(AniListImportSection, {
      global: { stubs: { RouterLink: { template: "<a><slot /></a>" } } },
    });
    await flushPromises();
    expect(
      (wrapper.get("#anilist-username").element as HTMLInputElement).value,
    ).toBe("someone");
    expect(wrapper.text()).toContain("Update existing titles");
    expect(wrapper.text()).toContain("Background tasks");
    expect(wrapper.text()).not.toContain("Import frequency");
    expect(wrapper.find("#anilist-hours").exists()).toBe(false);
    expect("anilist_import_enabled" in DEFAULT_PREFERENCES).toBe(false);
  });
});
