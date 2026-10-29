// @vitest-environment happy-dom
import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";
import Setup from "../views/Setup.vue";
import AcceptInvite from "../views/AcceptInvite.vue";
import PasswordReset from "../views/PasswordReset.vue";
import { createInitialAdmin, fetchSetupConfiguration } from "../services/setup";
import { invitationStatus } from "../services/userInvitations";
import { passwordResetStatus } from "../services/passwordRecovery";

vi.mock("vue-router", () => ({
  useRoute: () => ({ query: {}, path: "/x", hash: "" }),
  useRouter: () => ({ replace: vi.fn() }),
}));
vi.mock("../state/auth", () => ({
  currentUser: { value: null },
  checkAuth: vi.fn(),
}));
vi.mock("../services/passwordPolicy", () => ({
  fetchPasswordPolicy: vi.fn(async () => null),
  passwordValidationErrors: vi.fn(() => []),
}));
vi.mock("../services/setup", () => ({
  fetchSetupStatus: vi.fn(async () => ({
    setup_required: true,
    startup_mode: "production",
    startup_ui_enabled: true,
  })),
  fetchSetupConfiguration: vi.fn(),
  saveSetupConfiguration: vi.fn(),
  createInitialAdmin: vi.fn(async () => undefined),
}));
vi.mock("../services/userInvitations", () => ({
  invitationStatus: vi.fn(),
  acceptInvitation: vi.fn(),
}));
vi.mock("../services/passwordRecovery", async (original) => ({
  ...(await original<typeof import("../services/passwordRecovery")>()),
  passwordResetStatus: vi.fn(),
}));

const stubs = { TrustedProxyControls: true, AppBrand: true };
const button = (wrapper: ReturnType<typeof mount>, text: string) =>
  wrapper.findAll("button").find((b) => b.text() === text);

beforeEach(() => vi.clearAllMocks());

describe("first-run setup when the configuration cannot be loaded", () => {
  it("shows the failure with Try again instead of a usable empty wizard", async () => {
    vi.mocked(fetchSetupConfiguration).mockRejectedValueOnce(
      new Error("Registry unavailable"),
    );
    const wrapper = mount(Setup, { global: { stubs } });
    await flushPromises();
    expect(wrapper.text()).toContain("Registry unavailable");
    expect(wrapper.find(".setup-nav").exists()).toBe(false);
    expect(wrapper.find("button.primary").exists()).toBe(false);
    expect(createInitialAdmin).not.toHaveBeenCalled();

    vi.mocked(fetchSetupConfiguration).mockResolvedValueOnce({
      sections: [],
      startup_mode: "production",
      startup_ui_enabled: true,
    });
    await button(wrapper, "Try again")!.trigger("click");
    await flushPromises();
    expect(wrapper.find(".setup-nav").exists()).toBe(true);
    expect(wrapper.text()).not.toContain("Registry unavailable");
  });
});

describe.each([
  ["accepting an invitation", AcceptInvite, invitationStatus],
  ["resetting a password", PasswordReset, passwordResetStatus],
] as const)("%s when the availability check fails", (_name, view, status) => {
  it("says the check failed and retries, rather than claiming it is disabled", async () => {
    vi.mocked(status)
      .mockRejectedValueOnce(new Error("offline"))
      .mockResolvedValueOnce(false);
    const wrapper = mount(view, {
      global: { stubs: { AppBrand: true, PasswordInput: true } },
    });
    await flushPromises();
    expect(wrapper.text()).toContain("Could not check whether");
    expect(wrapper.text()).not.toContain("unavailable. Contact");

    await button(wrapper, "Try again")!.trigger("click");
    await flushPromises();
    // a real "disabled" answer is still reported as unavailable
    expect(wrapper.text()).toContain("unavailable");
    expect(wrapper.text()).not.toContain("Could not check whether");
  });
});
