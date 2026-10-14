// @vitest-environment happy-dom

import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, expect, it, vi } from "vitest";
import OidcSettingsSection from "../components/settings/OidcSettingsSection.vue";
import {
  fetchDeploymentSettings,
  updateDeploymentSettings,
  type DeploymentSettings,
  type OidcProviderSetting,
} from "../services/deploymentSettings";

vi.mock("../services/deploymentSettings", () => ({
  fetchDeploymentSettings: vi.fn(),
  updateDeploymentSettings: vi.fn(),
}));

afterEach(() => vi.clearAllMocks());

function deployment(required = false): DeploymentSettings {
  const provider: OidcProviderSetting = {
    name: "Example",
    slug: "example",
    issuer_url: "https://identity.example.test",
    client_id: "client",
    client_secret: "",
    scopes: "openid profile email",
    redirect_uri: null,
    groups_claim: "groups",
    admin_group: null,
    user_match_field: "email",
    allow_new_users: true,
    require_verified_email: required,
    button_text: "Continue with SSO",
    button_colour: "",
    button_image_url: null,
    enabled: true,
    show_on_login: true,
    autostart_enabled: true,
    client_secret_configured: true,
  };
  return {
    providers: {},
    provider_locks: {},
    real_ip: {
      header: "",
      trusted_proxies: "",
      locked: { header: false, trusted_proxies: false },
    },
    oidc: {
      enabled: true,
      issuer_url: null,
      client_id: null,
      scopes: null,
      redirect_uri: null,
      groups_claim: null,
      admin_group: null,
      user_match_field: null,
      default_login_method: "local",
      login_button_text: "Continue with SSO",
      allow_new_users: true,
      require_verified_email: false,
      client_secret_configured: false,
      named_providers: [provider],
      locked_fields: {},
    },
  };
}

async function settings(result: DeploymentSettings) {
  vi.mocked(fetchDeploymentSettings).mockResolvedValue(result);
  vi.mocked(updateDeploymentSettings).mockResolvedValue(result);
  const wrapper = mount(OidcSettingsSection);
  await flushPromises();
  return wrapper;
}

it("keeps verification optional for new providers and older responses", async () => {
  const result = deployment();
  Reflect.deleteProperty(
    result.oidc.named_providers[0]!,
    "require_verified_email",
  );
  const wrapper = await settings(result);
  const requirement = wrapper
    .findAll("label")
    .find((label) => label.text().includes("Require verified email"))!;
  expect((requirement.get("input").element as HTMLInputElement).checked).toBe(
    false,
  );
  await wrapper
    .findAll("button")
    .find((button) => button.text() === "+ Add provider")!
    .trigger("click");
  const checkboxes = wrapper
    .findAll("label")
    .filter((label) => label.text().includes("Require verified email"));
  expect(checkboxes).toHaveLength(2);
  expect(
    (checkboxes[1]!.get("input").element as HTMLInputElement).checked,
  ).toBe(false);
  expect(wrapper.text()).toContain(
    "false, missing or malformed claims are rejected",
  );
  wrapper.unmount();
});

it.each([false, true])(
  "loads a saved requirement and persists an explicit toggle from %s",
  async (required) => {
    const wrapper = await settings(deployment(required));
    const checkbox = wrapper
      .findAll("label")
      .find((label) => label.text().includes("Require verified email"))!
      .get("input");
    expect((checkbox.element as HTMLInputElement).checked).toBe(required);
    await checkbox.setValue(!required);
    await wrapper
      .findAll("button")
      .find((button) => button.text() === "Save OIDC settings")!
      .trigger("click");
    await flushPromises();
    const payload = vi.mocked(updateDeploymentSettings).mock.calls[0]![0];
    const providers: OidcProviderSetting[] = JSON.parse(
      String(payload.oidc_providers_json),
    );
    expect(providers[0]!.require_verified_email).toBe(!required);
    expect(providers[0]!.client_secret).toBe("");
    wrapper.unmount();
  },
);
