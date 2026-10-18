// @vitest-environment happy-dom
import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { defineComponent, h } from "vue";
import MetadataAvailabilityNotice from "../components/MetadataAvailabilityNotice.vue";
import { useMetadataAvailability } from "../composables/useMetadataAvailability";
import { preferences } from "../state/preferences";
import { DEFAULT_PREFERENCES } from "../services/preferences";
import {
  fetchMetadataProviders,
  type MetadataProviderStatus,
} from "../services/metadata";
import { searchAvailability } from "../utils/metadataSearchAvailability";
import { announceMetadataProvidersChanged } from "../utils/metadataProviderEvents";

vi.mock("../state/auth", async () => {
  const { ref } = await import("vue");
  return { currentUser: ref({ id: "owner", is_admin: true }) };
});
vi.mock("../services/metadata", () => ({
  fetchMetadataProviders: vi.fn(),
}));
vi.mock("vue-router", () => ({
  RouterLink: {
    props: ["to"],
    template: '<a :href="to"><slot /></a>',
  },
}));

function provider(
  overrides: Partial<MetadataProviderStatus> = {},
): MetadataProviderStatus {
  return {
    provider_id: "core.igdb",
    plugin_id: "host.metadata",
    name: "IGDB",
    included: true,
    account_enabled: true,
    media_types: ["game"],
    state: "healthy",
    checked_at: null,
    failure: null,
    configured_fields: {},
    operations: {
      search: "search",
      metadata: null,
      media: null,
      health: "health",
    },
    configuration: [
      {
        key: "client_secret",
        label: "Client secret",
        scope: "system",
        required: true,
        secret: true,
      },
    ],
    ...overrides,
  };
}

describe("searchAvailability", () => {
  it("names the missing provider when there are none", () => {
    const admin = searchAvailability([], ["game"], true);
    expect(admin.available).toBe(false);
    expect(admin.reason).toBe("No metadata provider is installed for Games.");
    expect(admin.action?.label).toBe("Plugin Manager");
    expect(searchAvailability([], ["game"], false).action).toBeNull();
  });

  it("allows searching with one eligible provider", () => {
    expect(searchAvailability([provider()], ["game"], false)).toEqual({
      available: true,
      reason: null,
      action: null,
    });
    // an unvalidated provider is still tried by searches
    expect(
      searchAvailability([provider({ state: "unvalidated" })], ["game"], false)
        .available,
    ).toBe(true);
  });

  it("ignores providers for other media types", () => {
    const found = searchAvailability(
      [provider({ media_types: ["movie"] })],
      ["game"],
      true,
    );
    expect(found.available).toBe(false);
    expect(found.reason).toContain("Games");
  });

  it("explains missing server credentials without exposing any value", () => {
    const admin = searchAvailability(
      [
        provider({
          state: "not_configured",
          configured_fields: { client_secret: { system: false, user: false } },
        }),
      ],
      ["game"],
      true,
    );
    expect(admin.reason).toContain("IGDB: needs server credentials");
    expect(admin.action?.label).toBe("Server integrations");
    const member = searchAvailability(
      [provider({ state: "not_configured" })],
      ["game"],
      false,
    );
    expect(member.reason).toContain("until an administrator adds them");
    expect(member.action).toBeNull();
  });

  it("explains a plugin that is disabled or has lost its permission", () => {
    for (const state of ["disabled", "plugin_unavailable"]) {
      const found = searchAvailability(
        [provider({ state, name: "Example plugin" })],
        ["game"],
        true,
      );
      expect(found.available).toBe(false);
      expect(found.reason).toContain("Example plugin");
      expect(found.action?.to).toContain("plugins");
    }
  });

  it("explains a provider the account turned off", () => {
    const found = searchAvailability(
      [provider()],
      ["game"],
      false,
      () => false,
    );
    expect(found.reason).toBe("IGDB is turned off for your account.");
    expect(found.action?.label).toBe("Metadata sources");
  });
});

describe("useMetadataAvailability", () => {
  beforeEach(() => {
    vi.mocked(fetchMetadataProviders).mockReset();
    preferences.value = { ...DEFAULT_PREFERENCES };
  });
  afterEach(() => vi.restoreAllMocks());

  function host() {
    return mount(
      defineComponent({
        setup() {
          const { availability } = useMetadataAvailability(["game"]);
          return () =>
            h("div", [
              h("input", { id: "title" }),
              h(MetadataAvailabilityNotice, {
                id: "why",
                availability: availability.value,
              }),
              h("button", { disabled: !availability.value.available }, "Go"),
            ]);
        },
      }),
      { attachTo: document.body },
    );
  }

  it("shows a visible reason with a link and keeps it when the list is stale", async () => {
    vi.mocked(fetchMetadataProviders).mockResolvedValue([]);
    const wrapper = host();
    await flushPromises();
    expect(wrapper.find("button").attributes("disabled")).toBeDefined();
    expect(wrapper.find("#why").text()).toContain("No metadata provider");
    expect(wrapper.find("#why").attributes("role")).toBe("status");
    wrapper.unmount();
  });

  it("refreshes after a provider is configured and keeps unsaved form data", async () => {
    vi.mocked(fetchMetadataProviders).mockResolvedValue([
      provider({ state: "not_configured" }),
    ]);
    const wrapper = host();
    await flushPromises();
    const input = wrapper.find("input");
    await input.setValue("Half-Life");
    expect(wrapper.find("#why").text()).toContain("IGDB");
    vi.mocked(fetchMetadataProviders).mockResolvedValue([provider()]);
    announceMetadataProvidersChanged();
    await flushPromises();
    expect(wrapper.find("#why").exists()).toBe(false);
    expect(wrapper.find("button").attributes("disabled")).toBeUndefined();
    expect((input.element as HTMLInputElement).value).toBe("Half-Life");
    wrapper.unmount();
  });

  it("goes unavailable again after the provider is disabled or removed", async () => {
    vi.mocked(fetchMetadataProviders).mockResolvedValue([provider()]);
    const wrapper = host();
    await flushPromises();
    expect(wrapper.find("#why").exists()).toBe(false);
    vi.mocked(fetchMetadataProviders).mockResolvedValue([]);
    window.dispatchEvent(new Event("focus"));
    await flushPromises();
    expect(wrapper.find("#why").exists()).toBe(true);
    wrapper.unmount();
  });

  it("follows the account's provider switch in preferences", async () => {
    vi.mocked(fetchMetadataProviders).mockResolvedValue([provider()]);
    const wrapper = host();
    await flushPromises();
    preferences.value = {
      ...preferences.value,
      metadata_providers_enabled: { "core.igdb": false },
    };
    await flushPromises();
    expect(wrapper.find("#why").text()).toContain(
      "turned off for your account",
    );
    wrapper.unmount();
  });

  it("allows searching, leaving the server to decide, when the list cannot load", async () => {
    vi.mocked(fetchMetadataProviders).mockRejectedValue(new Error("offline"));
    const wrapper = host();
    await flushPromises();
    expect(wrapper.find("#why").exists()).toBe(false);
    expect(wrapper.find("button").attributes("disabled")).toBeUndefined();
    wrapper.unmount();
  });
});
