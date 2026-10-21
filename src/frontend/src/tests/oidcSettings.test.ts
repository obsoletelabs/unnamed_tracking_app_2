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

afterEach(() => {
  vi.clearAllMocks();
  vi.unstubAllGlobals();
});

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
  const wrapper = mount(OidcSettingsSection, { attachTo: document.body });
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

function withProviders(
  ...entries: Array<Partial<OidcProviderSetting>>
): DeploymentSettings {
  const result = deployment();
  const template = result.oidc.named_providers[0]!;
  result.oidc.named_providers = entries.map((entry) => ({
    ...template,
    ...entry,
  }));
  return result;
}

const threeProviders = () =>
  withProviders(
    { name: "Authentik", slug: "authentik" },
    { name: "Keycloak", slug: "keycloak", show_on_login: false },
    { name: "Pocket ID", slug: "pocket-id" },
  );

type Wrapper = Awaited<ReturnType<typeof settings>>;

function cards(wrapper: Wrapper) {
  return wrapper.findAll(".provider-card");
}

function card(wrapper: Wrapper, name: string) {
  return cards(wrapper).find(
    (item) => item.get(".provider-identity strong").text() === name,
  )!;
}

function order(wrapper: Wrapper) {
  return cards(wrapper).map((item) =>
    item.get(".provider-identity strong").text(),
  );
}

function toggle(wrapper: Wrapper, name: string) {
  return card(wrapper, name).get("button[aria-controls]");
}

function body(wrapper: Wrapper, name: string) {
  const id = toggle(wrapper, name).attributes("aria-controls")!;
  return card(wrapper, name).get(`[id="${id}"]`);
}

function isOpen(wrapper: Wrapper, name: string) {
  const expanded = toggle(wrapper, name).attributes("aria-expanded");
  // v-show hides collapsed settings from view, keyboard and assistive tech.
  const hidden =
    (body(wrapper, name).element as HTMLElement).style.display === "none";
  expect(hidden).toBe(expanded !== "true");
  return expanded === "true";
}

function button(wrapper: Wrapper, label: string) {
  return wrapper
    .findAll("button")
    .find(
      (item) =>
        item.attributes("aria-label") === label || item.text() === label,
    )!;
}

async function saveSettings(wrapper: Wrapper) {
  await button(wrapper, "Save OIDC settings").trigger("click");
  await flushPromises();
  const payload = vi.mocked(updateDeploymentSettings).mock.calls.at(-1)![0];
  return JSON.parse(
    String(payload.oidc_providers_json),
  ) as OidcProviderSetting[];
}

function echoSavedProviders() {
  vi.mocked(updateDeploymentSettings).mockImplementation(async (payload) =>
    withProviders(
      ...(
        JSON.parse(String(payload.oidc_providers_json)) as OidcProviderSetting[]
      ).map((provider) => ({ ...provider, client_secret: "" })),
    ),
  );
}

it("loads saved providers as collapsed rows that keep identity and reorder controls", async () => {
  const wrapper = await settings(threeProviders());
  expect(order(wrapper)).toEqual(["Authentik", "Keycloak", "Pocket ID"]);
  for (const name of ["Authentik", "Keycloak", "Pocket ID"]) {
    const control = toggle(wrapper, name);
    expect(control.element.tagName).toBe("BUTTON");
    expect(control.attributes("type")).toBe("button");
    expect(control.element.closest("h4")).not.toBeNull();
    expect(isOpen(wrapper, name)).toBe(false);
    const region = body(wrapper, name);
    expect(region.attributes("role")).toBe("group");
    const labelId = region.attributes("aria-labelledby")!;
    expect(wrapper.get(`[id="${labelId}"]`).text()).toBe(name);
    expect(card(wrapper, name).get(".provider-card-head").isVisible()).toBe(
      true,
    );
    expect(
      card(wrapper, name).get(".provider-card-head").attributes("draggable"),
    ).toBe("true");
    expect(button(wrapper, `Move ${name} up`).isVisible()).toBe(true);
    expect(button(wrapper, `Move ${name} down`).isVisible()).toBe(true);
    expect(button(wrapper, `Remove ${name}`).isVisible()).toBe(true);
  }
  expect(toggle(wrapper, "Keycloak").text()).toContain("/keycloak");
  expect(toggle(wrapper, "Keycloak").text()).toContain("Hidden from login");
  expect(toggle(wrapper, "Authentik").text()).not.toContain("Hidden");
  expect(
    button(wrapper, "Move Authentik up").attributes("disabled"),
  ).toBeDefined();
  expect(
    button(wrapper, "Move Pocket ID down").attributes("disabled"),
  ).toBeDefined();
  expect(wrapper.get(".drag-handle").attributes("aria-hidden")).toBe("true");
  wrapper.unmount();
});

it("expands and collapses one provider without losing unsaved edits", async () => {
  const wrapper = await settings(threeProviders());
  await toggle(wrapper, "Keycloak").trigger("click");
  expect(isOpen(wrapper, "Keycloak")).toBe(true);
  expect(isOpen(wrapper, "Authentik")).toBe(false);
  expect(isOpen(wrapper, "Pocket ID")).toBe(false);

  const clientId = body(wrapper, "Keycloak")
    .findAll("label")
    .find((label) => label.text() === "Client ID")!
    .get("input");
  await clientId.setValue("edited-client");
  await toggle(wrapper, "Keycloak").trigger("click");
  expect(isOpen(wrapper, "Keycloak")).toBe(false);
  await toggle(wrapper, "Keycloak").trigger("click");
  expect(isOpen(wrapper, "Keycloak")).toBe(true);
  expect((clientId.element as HTMLInputElement).value).toBe("edited-client");

  const saved = await saveSettings(wrapper);
  expect(saved.map((provider) => provider.client_id)).toEqual([
    "client",
    "edited-client",
    "client",
  ]);
  wrapper.unmount();
});

it("reorders collapsed providers with the arrow buttons, keeping focus and announcing the move", async () => {
  const wrapper = await settings(threeProviders());
  await toggle(wrapper, "Keycloak").trigger("click");

  const moveUp = button(wrapper, "Move Pocket ID up");
  (moveUp.element as HTMLButtonElement).focus();
  await moveUp.trigger("click");
  await flushPromises();
  expect(order(wrapper)).toEqual(["Authentik", "Pocket ID", "Keycloak"]);
  expect(document.activeElement).toBe(
    button(wrapper, "Move Pocket ID up").element,
  );
  expect(wrapper.get("[role=status]").text()).toBe(
    "Pocket ID moved to position 2 of 3.",
  );
  expect(isOpen(wrapper, "Keycloak")).toBe(true);
  expect(isOpen(wrapper, "Pocket ID")).toBe(false);

  await button(wrapper, "Move Pocket ID up").trigger("click");
  await flushPromises();
  expect(order(wrapper)).toEqual(["Pocket ID", "Authentik", "Keycloak"]);
  // Move up is now disabled, so focus stays with the provider's other arrow.
  expect(document.activeElement).toBe(
    button(wrapper, "Move Pocket ID down").element,
  );
  expect(wrapper.get("[role=status]").text()).toBe(
    "Pocket ID moved to position 1 of 3.",
  );

  echoSavedProviders();
  const saved = await saveSettings(wrapper);
  expect(saved.map((provider) => provider.slug)).toEqual([
    "pocket-id",
    "authentik",
    "keycloak",
  ]);
  expect(order(wrapper)).toEqual(["Pocket ID", "Authentik", "Keycloak"]);
  expect(isOpen(wrapper, "Keycloak")).toBe(true);
  expect(isOpen(wrapper, "Pocket ID")).toBe(false);
  expect(wrapper.text()).toContain("OIDC settings saved.");
  wrapper.unmount();

  const reloaded = await settings(
    withProviders(...saved.map((provider) => ({ ...provider }))),
  );
  expect(order(reloaded)).toEqual(["Pocket ID", "Authentik", "Keycloak"]);
  expect(
    ["Pocket ID", "Authentik", "Keycloak"].map((name) =>
      isOpen(reloaded, name),
    ),
  ).toEqual([false, false, false]);
  reloaded.unmount();
});

it("drags a collapsed provider into a new position", async () => {
  const wrapper = await settings(threeProviders());
  await card(wrapper, "Pocket ID")
    .get(".provider-card-head")
    .trigger("dragstart");
  expect(card(wrapper, "Pocket ID").classes()).toContain("dragging");
  await card(wrapper, "Authentik").trigger("drop");
  expect(order(wrapper)).toEqual(["Pocket ID", "Authentik", "Keycloak"]);
  expect(card(wrapper, "Pocket ID").classes()).not.toContain("dragging");
  expect(wrapper.get("[role=status]").text()).toBe(
    "Pocket ID moved to position 1 of 3.",
  );
  const saved = await saveSettings(wrapper);
  expect(saved.map((provider) => provider.slug)).toEqual([
    "pocket-id",
    "authentik",
    "keycloak",
  ]);
  wrapper.unmount();
});

it("expands or collapses every provider at once", async () => {
  const wrapper = await settings(threeProviders());
  const names = ["Authentik", "Keycloak", "Pocket ID"];
  await button(wrapper, "Expand all").trigger("click");
  expect(names.map((name) => isOpen(wrapper, name))).toEqual([
    true,
    true,
    true,
  ]);
  await toggle(wrapper, "Keycloak").trigger("click");
  await button(wrapper, "Collapse all").trigger("click");
  expect(names.map((name) => isOpen(wrapper, name))).toEqual([
    false,
    false,
    false,
  ]);
  expect(button(wrapper, "Expand all")).toBeDefined();
  wrapper.unmount();

  const single = await settings(deployment());
  expect(button(single, "Expand all")).toBeUndefined();
  expect(button(single, "Collapse all")).toBeUndefined();
  single.unmount();
});

it("opens newly added providers for editing while saved ones stay collapsed", async () => {
  const wrapper = await settings(deployment());
  await button(wrapper, "+ Add provider").trigger("click");
  expect(isOpen(wrapper, "Example")).toBe(false);
  expect(isOpen(wrapper, "Provider 2")).toBe(true);
  expect(button(wrapper, "Collapse all")).toBeDefined();
  wrapper.unmount();
});

it("copies the current generated URI after an unsaved slug edit without saving settings", async () => {
  const writeText = vi.fn().mockResolvedValue(undefined);
  vi.stubGlobal("navigator", { clipboard: { writeText } });
  const wrapper = await settings(
    withProviders({
      name: "Example",
      slug: "example",
      redirect_uri: "https://old.example.test/api/auth/oidc/callback/old",
    }),
  );
  await toggle(wrapper, "Example").trigger("click");
  const slug = body(wrapper, "Example")
    .findAll("label")
    .find((label) => label.text() === "Slug")!
    .get("input");
  await slug.setValue("renamed");
  await button(wrapper, "Copy redirect URI for Example").trigger("click");
  await flushPromises();
  expect(writeText).toHaveBeenCalledWith(
    `${window.location.origin}/api/auth/oidc/callback/renamed`,
  );
  expect(wrapper.get("[aria-live=polite].field-hint").text()).toBe(
    "Redirect URI copied.",
  );
  expect(updateDeploymentSettings).not.toHaveBeenCalled();
  await slug.setValue("changed-again");
  expect(wrapper.get("[aria-live=polite].field-hint").text()).toBe("");
  wrapper.unmount();
});

it.each(["blocked", "unavailable"])(
  "offers manual copying when clipboard access is %s",
  async (mode) => {
    vi.stubGlobal(
      "navigator",
      mode === "blocked"
        ? {
            clipboard: {
              writeText: vi.fn().mockRejectedValue(new Error("Denied")),
            },
          }
        : {},
    );
    const wrapper = await settings(deployment());
    await toggle(wrapper, "Example").trigger("click");
    await button(wrapper, "Copy redirect URI for Example").trigger("click");
    await flushPromises();
    expect(wrapper.get("[aria-live=polite].field-hint").text()).toBe(
      "Could not copy. Select the redirect URI and copy it manually.",
    );
    expect(wrapper.get("input[readonly]").attributes("aria-readonly")).toBe(
      "true",
    );
    expect(updateDeploymentSettings).not.toHaveBeenCalled();
    wrapper.unmount();
  },
);
