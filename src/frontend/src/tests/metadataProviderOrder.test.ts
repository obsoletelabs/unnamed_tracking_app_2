// @vitest-environment happy-dom
import { flushPromises, mount, type VueWrapper } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import ScanSettingsSection from "../components/settings/ScanSettingsSection.vue";
import { currentUser } from "../state/auth";
import {
  fetchScanSettings,
  updateScanSettings,
  type ScanSettings,
} from "../services/settings";
import {
  fetchMetadataProviders,
  type MetadataMediaType,
  type MetadataProviderStatus,
} from "../services/metadata";
import {
  effectiveProviderOrder,
  lastUsedLabel,
  moveProvider,
  providerAvailability,
} from "../utils/metadataProviders";

vi.mock("../state/auth", async () => {
  const { ref } = await import("vue");
  return { currentUser: ref({ id: "owner", is_admin: true }) };
});
vi.mock("../services/settings", () => ({
  fetchScanSettings: vi.fn(),
  updateScanSettings: vi.fn(),
}));
vi.mock("../services/metadata", () => ({ fetchMetadataProviders: vi.fn() }));

const ALL = {
  search: "search",
  metadata: "metadata",
  media: "media",
  health: "health",
};
function provider(
  provider_id: string,
  name: string,
  media_types: MetadataMediaType[],
  fields: Partial<MetadataProviderStatus> = {},
): MetadataProviderStatus {
  return {
    provider_id,
    plugin_id: provider_id.startsWith("core.")
      ? "host.metadata"
      : provider_id.split(".").slice(0, 2).join("."),
    name,
    included: provider_id.startsWith("core."),
    account_enabled: true,
    media_types,
    state: "healthy",
    checked_at: null,
    failure: null,
    configured_fields: {},
    operations: { ...ALL },
    configuration: [],
    priority: 100,
    artwork_priority: 100,
    ...fields,
  };
}

const igdb = provider("core.igdb", "IGDB", ["game"], {
  priority: 0,
  state: "not_configured",
  configuration: [
    {
      key: "client_id",
      label: "Client ID",
      scope: "system",
      required: true,
      secret: false,
    },
  ],
});
const steam = provider("core.steam", "Steam", ["game"], {
  priority: 2,
  artwork_priority: 1,
});
const grid = provider("core.steamgriddb", "SteamGridDB", ["game"], {
  operations: { search: null, metadata: null, media: "media", health: "h" },
  artwork_priority: 0,
});
const gog = provider("official.metadata-gog.gog", "GOG", ["game"], {
  priority: 3,
  operations: { ...ALL, media: null },
  state: "disabled",
});
const giantBomb = provider(
  "official.metadata-giantbomb.giantbomb",
  "GiantBomb",
  ["game"],
  { priority: 1, account_enabled: false, state: "disabled" },
);
const tvmaze = provider("core.tvmaze", "TVmaze", ["tv_show"]);
const omdb = provider("core.omdb", "OMDb", ["movie", "tv_show"]);
const tmdb = provider("official.metadata-tmdb.tmdb", "TMDB", [
  "movie",
  "tv_show",
  "anime",
]);
const anilist = provider("core.anilist", "AniList", ["anime"]);
const providers = [igdb, steam, grid, gog, giantBomb, tvmaze, omdb, tmdb];

const settings: ScanSettings = {
  id: "scan",
  user_id: "owner",
  provider_order: [
    "IGDB",
    "GiantBomb",
    "Steam",
    "GOG",
    "RetroAchievements",
    "HowLongToBeat",
  ],
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
  provider_last_used: { Steam: 1, "core.igdb": 0 },
  created_at: 0,
  updated_at: 0,
};

describe("provider order helpers", () => {
  it("orders by the server's ranks and keeps entries for absent providers", () => {
    expect(
      effectiveProviderOrder(providers, "metadata", settings.provider_order),
    ).toEqual([
      "core.igdb",
      "official.metadata-giantbomb.giantbomb",
      "core.steam",
      "official.metadata-gog.gog",
      "core.omdb",
      "core.tvmaze",
      "official.metadata-tmdb.tmdb",
      "RetroAchievements",
      "HowLongToBeat",
    ]);
    expect(
      effectiveProviderOrder(
        providers,
        "artwork",
        settings.image_provider_order,
      ),
    ).toEqual([
      "core.steamgriddb",
      "core.steam",
      "core.igdb",
      "core.omdb",
      "core.tvmaze",
      "official.metadata-giantbomb.giantbomb",
      "official.metadata-tmdb.tmdb",
      "ScreenScraper",
    ]);
  });

  it("moves within one media type without reordering the others when avoidable", () => {
    const types = new Map(
      [tvmaze, anilist, tmdb].map((item) => [
        item.provider_id,
        item.media_types,
      ]),
    );
    const order = [
      "core.tvmaze",
      "core.anilist",
      "official.metadata-tmdb.tmdb",
    ];
    // TV shows TVmaze, TMDB. Moving TMDB up past TVmaze must keep anime as AniList, TMDB.
    const next = moveProvider(
      order,
      ["core.tvmaze", "official.metadata-tmdb.tmdb"],
      "official.metadata-tmdb.tmdb",
      -1,
      types,
    )!;
    expect(next).toEqual([
      "core.anilist",
      "official.metadata-tmdb.tmdb",
      "core.tvmaze",
    ]);
    expect(
      moveProvider(order, ["core.tvmaze"], "core.tvmaze", -1, types),
    ).toBeNull();
    // Without a conflict the clicked provider moves.
    expect(
      moveProvider(
        ["a", "hidden", "b"],
        ["a", "b"],
        "a",
        1,
        new Map([
          ["a", ["game"]],
          ["b", ["game"]],
          ["hidden", ["movie"]],
        ]),
      ),
    ).toEqual(["hidden", "b", "a"]);
  });

  it("explains server availability with fixes only an administrator can make", () => {
    expect(providerAvailability(steam, false)).toMatchObject({
      tone: "ready",
      label: "Ready",
    });
    expect(providerAvailability(igdb, true).action?.label).toBe(
      "Server integrations",
    );
    expect(providerAvailability(igdb, false)).toMatchObject({
      action: null,
      label: "Needs server credentials",
      hint: "Skipped until an administrator adds them",
    });
    expect(providerAvailability(gog, true)).toMatchObject({
      tone: "unavailable",
      label: "Disabled on this server",
      action: { label: "Plugin Manager" },
    });
    expect(providerAvailability(gog, false).action).toBeNull();
    const personal = provider("core.omdb", "OMDb", ["movie"], {
      state: "not_configured",
      configuration: [
        {
          key: "api_key",
          label: "Key",
          scope: "both",
          required: true,
          secret: true,
        },
      ],
    });
    expect(providerAvailability(personal, false).action?.label).toBe(
      "Add credentials",
    );
    expect(
      providerAvailability({ ...tvmaze, state: "rate_limited" }, false).tone,
    ).toBe("warning");
  });

  it("reads provider use by ID before older display-name keys", () => {
    expect(lastUsedLabel(steam, { Steam: 940 }, 1000)).toBe("Used 1m ago");
    expect(lastUsedLabel(steam, { Steam: 1, "core.steam": 995 }, 1000)).toBe(
      "Used just now",
    );
    expect(lastUsedLabel(igdb, {}, 1000)).toBe("Not used yet");
  });
});

const wrappers: VueWrapper[] = [];
async function mountSection() {
  const wrapper = mount(ScanSettingsSection, {
    global: {
      stubs: {
        RouterLink: { props: ["to"], template: "<a :href='to'><slot /></a>" },
      },
    },
  });
  wrappers.push(wrapper);
  await flushPromises();
  return wrapper;
}
function names(wrapper: VueWrapper, id: string) {
  return wrapper
    .findAll(`[aria-labelledby="${id}-label"] .provider-title`)
    .map((item) => item.text());
}

describe("Scan & providers", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    currentUser.value = { id: "owner", is_admin: true } as never;
    vi.mocked(fetchScanSettings).mockResolvedValue(structuredClone(settings));
    vi.mocked(updateScanSettings).mockImplementation(async (payload) => ({
      ...settings,
      ...payload,
    }));
    vi.mocked(fetchMetadataProviders).mockResolvedValue(
      structuredClone(providers),
    );
  });
  afterEach(() => wrappers.splice(0).forEach((wrapper) => wrapper.unmount()));

  it("lists the registry providers this account uses, in the saved order", async () => {
    const wrapper = await mountSection();
    expect(names(wrapper, "metadata-provider-order")).toEqual([
      "IGDB",
      "Steam",
      "GOG",
    ]);
    expect(names(wrapper, "artwork-provider-order")).toEqual([
      "SteamGridDB",
      "Steam",
      "IGDB",
    ]);
    const text = wrapper.text();
    expect(text).not.toContain("RetroAchievements");
    expect(text).toContain("Not used by your account: GiantBomb.");
    expect(text).toContain("Disabled on this server");
    expect(text).toContain("Plugin");
    expect(wrapper.find('a[href*="section=plugins"]').exists()).toBe(true);
    expect(text).toContain("Used");
  });

  it("gives other accounts the same state without administrator links", async () => {
    currentUser.value = { id: "member", is_admin: false } as never;
    const wrapper = await mountSection();
    expect(names(wrapper, "metadata-provider-order")).toEqual([
      "IGDB",
      "Steam",
      "GOG",
    ]);
    expect(wrapper.text()).toContain(
      "Skipped until an administrator enables its plugin",
    );
    expect(wrapper.text()).toContain(
      "Skipped until an administrator adds them",
    );
    expect(wrapper.find('a[href*="area=administration"]').exists()).toBe(false);
  });

  it("switches media types and saves the whole plugin-aware order", async () => {
    const wrapper = await mountSection();
    const tv = wrapper
      .findAll(".segment")
      .find((button) => button.text() === "TV")!;
    await tv.trigger("click");
    expect(names(wrapper, "metadata-provider-order")).toEqual([
      "OMDb",
      "TVmaze",
      "TMDB",
    ]);
    await wrapper
      .get('[aria-label="Move TMDB up in Search & metadata order"]')
      .trigger("click");
    expect(names(wrapper, "metadata-provider-order")).toEqual([
      "OMDb",
      "TMDB",
      "TVmaze",
    ]);
    await wrapper
      .findAll(".segment")
      .find((b) => b.text() === "Games")!
      .trigger("click");
    await wrapper
      .get('[aria-label="Move GOG up in Search & metadata order"]')
      .trigger("click");
    await wrapper.get("button.primary-button").trigger("click");
    await flushPromises();
    const payload = vi.mocked(updateScanSettings).mock.calls[0][0];
    expect(payload.provider_order).toEqual([
      "core.igdb",
      "official.metadata-giantbomb.giantbomb",
      "official.metadata-gog.gog",
      "core.steam",
      "core.omdb",
      "official.metadata-tmdb.tmdb",
      "core.tvmaze",
      "RetroAchievements",
      "HowLongToBeat",
    ]);
    // Only the moved order is sent; the untouched artwork order stays as saved.
    expect(payload).not.toHaveProperty("image_provider_order");
    expect(wrapper.text()).toContain("Scan settings saved.");
  });

  it("saves a moved artwork order and keeps entries for absent providers", async () => {
    const wrapper = await mountSection();
    await wrapper
      .get('[aria-label="Move Steam up in Artwork order"]')
      .trigger("click");
    expect(names(wrapper, "artwork-provider-order")).toEqual([
      "Steam",
      "SteamGridDB",
      "IGDB",
    ]);
    await wrapper.get("button.primary-button").trigger("click");
    await flushPromises();
    const payload = vi.mocked(updateScanSettings).mock.calls[0][0];
    expect(payload).not.toHaveProperty("provider_order");
    expect(payload.image_provider_order?.slice(0, 2)).toEqual([
      "core.steam",
      "core.steamgriddb",
    ]);
    expect(payload.image_provider_order?.at(-1)).toBe("ScreenScraper");
  });

  it("does not rewrite saved orders when only field choices change", async () => {
    const wrapper = await mountSection();
    await wrapper.get("button.primary-button").trigger("click");
    await flushPromises();
    const payload = vi.mocked(updateScanSettings).mock.calls[0][0];
    expect(payload).not.toHaveProperty("provider_order");
    expect(payload).not.toHaveProperty("image_provider_order");
    expect(payload.save_developer).toBe(true);
  });

  it("keeps field choices usable when provider status cannot load", async () => {
    vi.mocked(fetchMetadataProviders).mockRejectedValue(
      new Error("Provider status is unavailable."),
    );
    const wrapper = await mountSection();
    expect(wrapper.find(".provider-list").exists()).toBe(false);
    expect(wrapper.get('[role="alert"]').text()).toContain(
      "Provider order cannot be changed right now.",
    );
    await wrapper.get("button.primary-button").trigger("click");
    await flushPromises();
    expect(vi.mocked(updateScanSettings).mock.calls[0][0]).not.toHaveProperty(
      "provider_order",
    );
  });
});
