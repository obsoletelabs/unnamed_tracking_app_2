// @vitest-environment happy-dom
import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";
import SteamTagsSection from "../components/settings/SteamTagsSection.vue";
import SteamWishlistSection from "../components/settings/SteamWishlistSection.vue";
import SteamConnectionDialog from "../components/settings/SteamConnectionDialog.vue";
import { fetchPreferences } from "../services/preferences";
import { fetchSteamConnection } from "../services/steamConnection";

vi.mock("../services/preferences", async (original) => ({
  ...(await original<typeof import("../services/preferences")>()),
  fetchPreferences: vi.fn(),
  queuePreferences: vi.fn(),
}));
vi.mock("../services/librarySync", () => ({ refreshSteamTags: vi.fn() }));
vi.mock("../services/steamConnection", async (original) => ({
  ...(await original<typeof import("../services/steamConnection")>()),
  fetchSteamConnection: vi.fn(),
  cancelSteamLogin: vi.fn(),
}));

beforeEach(() => vi.clearAllMocks());

describe.each([
  [
    "Steam wishlist",
    SteamWishlistSection,
    { steam_import_wishlist: true, steam_user_tags: true },
  ],
  [
    "Steam tags",
    SteamTagsSection,
    { steam_import_wishlist: false, steam_user_tags: false },
  ],
] as const)(
  "%s preference when it cannot be loaded",
  (_name, section, stored) => {
    it("keeps the switch locked until the real value is read, then recovers", async () => {
      vi.mocked(fetchPreferences)
        .mockRejectedValueOnce(new Error("Settings unavailable"))
        .mockResolvedValueOnce(stored as never);
      const wrapper = mount(section);
      await flushPromises();
      const toggle = () => wrapper.get('[role="switch"]');
      expect(wrapper.text()).toContain("Settings unavailable");
      expect(toggle().attributes("disabled")).toBeDefined();

      await wrapper
        .findAll("button")
        .find((b) => b.text() === "Retry")!
        .trigger("click");
      await flushPromises();
      expect(wrapper.text()).not.toContain("Settings unavailable");
      expect(toggle().attributes("disabled")).toBeUndefined();
    });
  },
);

describe("Steam connection dialog when the connection cannot be loaded", () => {
  it("offers Try again and no sign-in form until the connection is known", async () => {
    vi.mocked(fetchSteamConnection)
      .mockRejectedValueOnce(new Error("Steam unavailable"))
      .mockResolvedValueOnce({
        connected: true,
        method: "account",
        display_name: "Gabe",
        expires_at: null,
        pending: null,
      });
    const wrapper = mount(SteamConnectionDialog, { attachTo: document.body });
    await flushPromises();
    const body = document.body;
    expect(body.textContent).toContain("Steam unavailable");
    expect(body.querySelector("form.login-form")).toBeNull();

    const retry = [...body.querySelectorAll("button")].find(
      (b) => b.textContent?.trim() === "Try again",
    )!;
    retry.click();
    await flushPromises();
    expect(body.textContent).toContain("Gabe connected");
    expect(body.textContent).not.toContain("Steam unavailable");
    wrapper.unmount();
  });
});
