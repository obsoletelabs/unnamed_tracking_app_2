// @vitest-environment happy-dom

import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, expect, it, vi } from "vitest";
import Login from "../views/Login.vue";
import { oidcLoginStatus } from "../services/oidc";
import { passwordResetStatus } from "../services/passwordRecovery";

const { route } = vi.hoisted(() => ({
  route: { path: "/login", query: {} as Record<string, string> },
}));
vi.mock("vue-router", () => ({
  useRoute: () => route,
  useRouter: () => ({ replace: vi.fn() }),
}));
vi.mock("../services/oidc", () => ({
  oidcLoginStatus: vi.fn(),
  oidcButtonStyle: vi.fn(() => ({})),
  startOidcLogin: vi.fn(),
}));
vi.mock("../services/auth", () => ({ login: vi.fn() }));
vi.mock("../state/auth", () => ({ checkAuth: vi.fn() }));
vi.mock("../services/passwordRecovery", () => ({
  passwordResetStatus: vi.fn(),
}));

afterEach(() => {
  vi.clearAllMocks();
  route.query = {};
});

it.each([
  [
    "provider_http_404",
    "The sign-in provider discovery endpoint was not found (HTTP 404). Ask an administrator to check the provider URL and whether OIDC is enabled.",
  ],
  ["provider_unavailable", "The SSO provider is currently unavailable."],
  [
    "dns_name_not_found",
    "The sign-in provider hostname could not be found. Ask an administrator to check the provider URL.",
  ],
  [
    "dns_temporary_failure",
    "The server temporarily could not look up the sign-in provider. Please try again shortly.",
  ],
  [
    "dns_lookup_failed",
    "The server could not look up the sign-in provider. Ask an administrator to check server DNS.",
  ],
  ["authentication_failed", "SSO authentication failed. Please try again."],
  ["https://secret.invalid/?token=never-render-this", "SSO sign-in failed."],
])(
  "shows fixed guidance for %s without rendering arbitrary error text",
  async (code, message) => {
    route.query = { oidc_error: code };
    vi.mocked(passwordResetStatus).mockResolvedValue(false);
    vi.mocked(oidcLoginStatus).mockResolvedValue({
      enabled: false,
      issuer: null,
      default_login_method: "local",
      login_button_text: "Continue with SSO",
      providers: [],
    });
    const wrapper = mount(Login, {
      global: {
        stubs: { AppBrand: true, PasswordInput: true, RouterLink: true },
      },
    });
    try {
      await flushPromises();
      expect(wrapper.text()).toContain(message);
      expect(wrapper.text()).not.toContain("never-render-this");
    } finally {
      wrapper.unmount();
    }
  },
);
