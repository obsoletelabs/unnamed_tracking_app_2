// @vitest-environment happy-dom
import { flushPromises, mount, type VueWrapper } from "@vue/test-utils";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import MetadataProviderSettings from "../components/plugins/MetadataProviderSettings.vue";
import { preferences, preferencesLoaded } from "../state/preferences";
import { DEFAULT_PREFERENCES, queuePreferences } from "../services/preferences";
import {
  fetchMetadataProviders,
  type MetadataProviderStatus,
} from "../services/metadata";

vi.mock("../state/auth", async () => {
  const { ref } = await import("vue");
  return { currentUser: ref({ id: "owner", is_admin: true }) };
});
vi.mock("../services/metadata", () => ({
  fetchMetadataProviders: vi.fn(),
  fetchServerAutomaticSearch: vi.fn().mockResolvedValue(true),
  saveServerAutomaticSearch: vi.fn(),
  saveMetadataProviderConfiguration: vi.fn(),
}));
vi.mock("../services/preferences", async (importOriginal) => {
  const original =
    await importOriginal<typeof import("../services/preferences")>();
  return { ...original, queuePreferences: vi.fn() };
});
const wrappers: VueWrapper[] = [];
const provider: MetadataProviderStatus = {
  provider_id: "core.steamgriddb",
  plugin_id: "host.metadata",
  name: "SteamGridDB",
  included: true,
  account_enabled: true,
  media_types: ["game"],
  state: "healthy",
  checked_at: null,
  failure: null,
  configured_fields: { api_key: { system: true, user: false } },
  operations: {
    search: "search",
    metadata: null,
    media: "media",
    health: "health",
  },
  configuration: [
    {
      key: "api_key",
      label: "API key",
      scope: "both",
      secret: true,
      required: true,
    },
  ],
};

beforeEach(() => {
  vi.clearAllMocks();
  preferences.value = {
    ...DEFAULT_PREFERENCES,
    metadata_providers_enabled: {},
    metadata_automatic_search_providers: { [provider.provider_id]: false },
  };
  preferencesLoaded.value = true;
  vi.mocked(fetchMetadataProviders).mockResolvedValue([provider]);
  vi.mocked(queuePreferences).mockImplementation(async (changes) => ({
    prefs: { ...preferences.value, ...changes },
    latest: true,
  }));
});
afterEach(() => {
  wrappers.splice(0).forEach((wrapper) => wrapper.unmount());
  preferences.value = { ...DEFAULT_PREFERENCES };
  preferencesLoaded.value = false;
});

it("lets the account opt out of a server-backed provider and preserves its typing choice", async () => {
  const wrapper = mount(MetadataProviderSettings, {
    global: { stubs: { RouterLink: true } },
  });
  wrappers.push(wrapper);
  await flushPromises();
  const toggle = wrapper.get(
    'input[aria-label="Use SteamGridDB for metadata"]',
  );
  await toggle.setValue(false);
  await flushPromises();
  expect(queuePreferences).toHaveBeenCalledWith({
    metadata_providers_enabled: { [provider.provider_id]: false },
  });
  expect(
    preferences.value.metadata_automatic_search_providers[provider.provider_id],
  ).toBe(false);
  expect(wrapper.text()).toContain("Disabled for your account");
  await wrapper
    .get('button[aria-label="Configure SteamGridDB"]')
    .trigger("click");
  expect(wrapper.text()).toContain(
    "ownership, playtime and achievement sync are unaffected",
  );
  const automatic = wrapper.findAll('.tile-form input[type="checkbox"]')[0]!;
  expect(automatic.attributes("disabled")).toBeDefined();
  await toggle.setValue(true);
  await flushPromises();
  expect(
    preferences.value.metadata_providers_enabled[provider.provider_id],
  ).toBe(true);
  expect(
    preferences.value.metadata_automatic_search_providers[provider.provider_id],
  ).toBe(false);
  expect(automatic.attributes("disabled")).toBeUndefined();
});

it("keeps account opt-outs separate from system credential administration", async () => {
  const wrapper = mount(MetadataProviderSettings, {
    props: { initialScope: "system" },
    global: { stubs: { RouterLink: true } },
  });
  wrappers.push(wrapper);
  await flushPromises();
  expect(
    wrapper.find('input[aria-label="Use SteamGridDB for metadata"]').exists(),
  ).toBe(false);
  expect(wrapper.text()).toContain("Automatic Search · server defaults");
});

it("reports a failed save and leaves the current choice available", async () => {
  vi.mocked(queuePreferences).mockRejectedValue(new Error("Could not save"));
  const wrapper = mount(MetadataProviderSettings, {
    global: { stubs: { RouterLink: true } },
  });
  wrappers.push(wrapper);
  await flushPromises();
  await wrapper
    .get('input[aria-label="Use SteamGridDB for metadata"]')
    .setValue(false);
  await flushPromises();
  expect(preferences.value.metadata_providers_enabled).toEqual({});
  expect(wrapper.text()).toContain("Could not save");
  expect(
    (
      wrapper.get('input[aria-label="Use SteamGridDB for metadata"]')
        .element as HTMLInputElement
    ).checked,
  ).toBe(true);
});
