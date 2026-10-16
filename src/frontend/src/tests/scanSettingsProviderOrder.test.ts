// @vitest-environment happy-dom
import { flushPromises, mount, type VueWrapper } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import ScanSettingsSection from "../components/settings/ScanSettingsSection.vue";
import {
  fetchMetadataProviders,
  orderMetadataProviders,
  type MetadataProviderStatus,
} from "../services/metadata";
import {
  fetchScanSettings,
  updateScanSettings,
  type ScanSettings,
} from "../services/settings";

vi.mock("../services/metadata", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../services/metadata")>()),
  fetchMetadataProviders: vi.fn(),
}));
vi.mock("../services/settings", () => ({
  fetchScanSettings: vi.fn(),
  updateScanSettings: vi.fn(),
}));

function provider(
  provider_id: string,
  name: string,
  changes: Partial<MetadataProviderStatus> = {},
): MetadataProviderStatus {
  return {
    provider_id,
    plugin_id: provider_id.startsWith("core.") ? "host.metadata" : "gog",
    name,
    account_enabled: true,
    media_types: ["game"],
    state: "healthy",
    checked_at: null,
    failure: null,
    configured_fields: {},
    operations: { search: "s", metadata: "m", media: null, health: null },
    configuration: [],
    ...changes,
  };
}

const PROVIDERS = [
  provider("core.tvmaze", "TVmaze", { media_types: ["tv_show"] }),
  provider("official.metadata-gog.provider", "GOG"),
  provider("core.steam", "Steam", { account_enabled: false }),
  provider("core.igdb", "IGDB", { state: "not_configured" }),
];

function settings(provider_order: string[]): ScanSettings {
  return {
    id: "scan",
    user_id: "owner",
    provider_order,
    image_provider_order: ["SteamGridDB", "ScreenScraper"],
    save_developer: true,
    save_publisher: true,
    save_series: true,
    save_tags: true,
    save_features: true,
    save_description: true,
    save_age_rating: true,
    save_release_date: true,
    save_time_to_beat: true,
    save_key_art: true,
    save_banner: true,
    save_logo: true,
    save_icon: true,
    provider_last_used: { "core.steam": Date.now() / 1000 },
    created_at: 0,
    updated_at: 0,
  };
}

let wrapper: VueWrapper | undefined;
beforeEach(() => vi.clearAllMocks());
afterEach(() => {
  wrapper?.unmount();
  wrapper = undefined;
});

async function mountSection(order: string[]) {
  vi.mocked(fetchScanSettings).mockResolvedValue(settings(order));
  wrapper = mount(ScanSettingsSection);
  await flushPromises();
  return wrapper;
}

const names = () =>
  wrapper!.findAll(".provider-item-label > span").map((item) => item.text());

describe("Scan settings provider order", () => {
  it("orders by saved IDs or legacy names, then the rest by provider ID", () => {
    const ids = (order: string[]) =>
      orderMetadataProviders(PROVIDERS, order).map((p) => p.provider_id);
    expect(ids(["core.igdb", "core.steam"])).toEqual([
      "core.igdb",
      "core.steam",
      "core.tvmaze",
      "official.metadata-gog.provider",
    ]);
    expect(ids(["GOG", "Steam", "GiantBomb"]).slice(0, 2)).toEqual([
      "official.metadata-gog.provider",
      "core.steam",
    ]);
    // An ID only ever names its own provider.
    expect(
      ids(["other.plugin.gog"]).indexOf("official.metadata-gog.provider"),
    ).toBe(3);
  });

  it("lists core and plugin providers and saves the reordered IDs", async () => {
    vi.mocked(fetchMetadataProviders).mockResolvedValue(PROVIDERS);
    await mountSection(["GOG", "core.steam"]);
    expect(names()).toEqual(["GOG", "Steam", "IGDB", "TVmaze"]);
    const text = wrapper!.text();
    expect(text).toContain("Games · off for you");
    expect(text).toContain("Games · needs setup");
    expect(text).toContain("used just now");

    await wrapper!.get('[aria-label="Move Steam up"]').trigger("click");
    expect(names()).toEqual(["Steam", "GOG", "IGDB", "TVmaze"]);
    await wrapper!.get("button.primary-button").trigger("click");
    await flushPromises();
    const sent = vi.mocked(updateScanSettings).mock.calls[0][0];
    expect(sent.provider_order).toEqual([
      "core.steam",
      "official.metadata-gog.provider",
      "core.igdb",
      "core.tvmaze",
    ]);
    expect(sent).not.toHaveProperty("image_provider_order");
  });

  it("keeps the saved order when providers cannot load", async () => {
    vi.mocked(fetchMetadataProviders).mockRejectedValue(new Error("down"));
    await mountSection(["core.steam"]);
    expect(wrapper!.text()).toContain("could not be loaded");
    await wrapper!.get("button.primary-button").trigger("click");
    await flushPromises();
    expect(vi.mocked(updateScanSettings).mock.calls[0][0]).not.toHaveProperty(
      "provider_order",
    );
  });
});
