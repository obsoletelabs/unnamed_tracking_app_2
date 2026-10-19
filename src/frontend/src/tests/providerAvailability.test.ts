// @vitest-environment happy-dom
import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import MediaMetadataSearch from "../components/MediaMetadataSearch.vue";
import { resetProviderAvailability } from "../composables/useProviderAvailability";
import {
  fetchMetadataProviders,
  type MetadataMediaType,
  type MetadataProviderStatus,
} from "../services/metadata";
import { announceProviderChange } from "../services/providerEvents";
import {
  PLUGINS_LINK,
  PROVIDERS_LINK,
  SOURCES_LINK,
  searchBlock,
} from "../services/providerAvailability";

vi.mock("../services/metadata", async (original) => ({
  ...(await original<typeof import("../services/metadata")>()),
  fetchMetadataProviders: vi.fn(),
}));

function provider(
  name: string,
  over: Partial<MetadataProviderStatus> = {},
): MetadataProviderStatus {
  return {
    provider_id: `core.${name.toLowerCase()}`,
    plugin_id: "core",
    name,
    media_types: ["movie"] as MetadataMediaType[],
    state: "healthy",
    checked_at: null,
    failure: null,
    account_enabled: true,
    configured_fields: {},
    operations: { search: "search", metadata: "m", media: null, health: null },
    configuration: [],
    ...over,
  } as MetadataProviderStatus;
}
const needsKey = (state = "not_configured") =>
  provider("TMDB", {
    state,
    configuration: [
      {
        key: "api_key",
        label: "an API key",
        scope: "system",
        required: true,
        secret: true,
      },
    ],
  });

describe("what blocks a search, and where to fix it (#254)", () => {
  it("lets a search run when any source for that media can", () => {
    expect(searchBlock([provider("TMDB")], "movie")).toBeNull();
    // a failing or busy source still counts: the search itself reports the failure
    expect(
      searchBlock([provider("TMDB", { state: "rate_limited" })], "movie"),
    ).toBeNull();
    expect(searchBlock([needsKey(), provider("OMDb")], "movie")).toBeNull();
  });

  it("names the missing setup without revealing anything secret", () => {
    const block = searchBlock([needsKey()], "movie");
    expect(block).toEqual({
      kind: "not_configured",
      message: "TMDB needs an API key.",
      setup: SOURCES_LINK,
    });
    const stored = needsKey();
    stored.configured_fields = { api_key: { system: true, user: false } };
    stored.state = "not_configured";
    // the key exists but the source still reports not configured: say so plainly
    expect(searchBlock([stored], "movie")?.message).toBe("TMDB is not set up.");
    expect(JSON.stringify(block)).not.toMatch(/secret|token|password/i);
  });

  it("separates 'none support it', 'turned off' and 'not running'", () => {
    expect(searchBlock([], "game")).toMatchObject({
      kind: "none",
      setup: null,
    });
    expect(searchBlock([], "game", true)).toMatchObject({
      kind: "none",
      setup: PLUGINS_LINK,
    });
    expect(
      searchBlock([provider("Other", { media_types: ["anime"] })], "movie")
        ?.kind,
    ).toBe("none");
    expect(
      searchBlock([provider("TMDB", { account_enabled: false })], "movie"),
    ).toMatchObject({ kind: "disabled", setup: PROVIDERS_LINK });
    expect(
      searchBlock([provider("TMDB", { state: "disabled" })], "movie")?.kind,
    ).toBe("disabled");
    expect(
      searchBlock([provider("Plug", { state: "plugin_unavailable" })], "movie"),
    ).toMatchObject({ kind: "unavailable", setup: null });
    expect(
      searchBlock(
        [provider("Plug", { state: "plugin_unavailable" })],
        "movie",
        true,
      )?.setup,
    ).toBe(PLUGINS_LINK);
  });

  it("only counts sources that can search", () => {
    const noSearch = provider("Art", {
      operations: { search: null, metadata: null, media: "m", health: null },
    });
    expect(searchBlock([noSearch], "movie")?.kind).toBe("none");
  });
});

describe("the search box", () => {
  beforeEach(() => resetProviderAvailability());
  afterEach(() => vi.clearAllMocks());

  const props = {
    label: "Search metadata providers",
    noun: "movie",
    results: [],
    searching: false,
    message: null,
    warnings: [],
    query: "dune",
    mediaType: "movie" as const,
  };

  it("disables Search and explains why, with a link that keeps the form open", async () => {
    vi.mocked(fetchMetadataProviders).mockResolvedValue([needsKey()]);
    const wrapper = mount(MediaMetadataSearch, { props });
    await flushPromises();
    const button = wrapper.get("button");
    expect(button.attributes("disabled")).toBeDefined();
    const note = wrapper.get(".provider-note");
    expect(note.text()).toContain("TMDB needs an API key.");
    expect(note.text()).toContain("fill in the details by hand");
    const link = note.get("a");
    expect(link.attributes("href")).toBe(
      "/settings?section=metadata&tab=sources",
    );
    expect(link.attributes("target")).toBe("_blank");
    // keyboard and screen readers get the same reason
    expect(button.attributes("aria-describedby")).toBe(note.attributes("id"));
    await wrapper.get("input").trigger("keydown.enter");
    expect(wrapper.emitted("search")).toBeUndefined();
  });

  it("searches normally when a source is ready, and clears the note once setup is done", async () => {
    vi.mocked(fetchMetadataProviders).mockResolvedValue([needsKey()]);
    const wrapper = mount(MediaMetadataSearch, { props });
    await flushPromises();
    expect(wrapper.find(".provider-note").exists()).toBe(true);
    // the key is saved elsewhere; the change is announced and the box re-checks
    vi.mocked(fetchMetadataProviders).mockResolvedValue([needsKey("healthy")]);
    announceProviderChange();
    await flushPromises();
    expect(wrapper.find(".provider-note").exists()).toBe(false);
    expect(wrapper.get("button").attributes("disabled")).toBeUndefined();
    await wrapper.get("button").trigger("click");
    expect(wrapper.emitted("search")).toHaveLength(1);
  });

  it("stays usable when the availability request itself fails (the server decides)", async () => {
    vi.mocked(fetchMetadataProviders).mockRejectedValue(new Error("offline"));
    const wrapper = mount(MediaMetadataSearch, { props });
    await flushPromises();
    expect(wrapper.find(".provider-note").exists()).toBe(false);
    expect(wrapper.get("button").attributes("disabled")).toBeUndefined();
  });

  it("does nothing extra without a media type", async () => {
    const wrapper = mount(MediaMetadataSearch, {
      props: { ...props, mediaType: undefined },
    });
    await flushPromises();
    expect(fetchMetadataProviders).not.toHaveBeenCalled();
    expect(wrapper.find(".provider-note").exists()).toBe(false);
  });
});
