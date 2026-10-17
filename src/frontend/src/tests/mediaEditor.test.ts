// @vitest-environment happy-dom
import { flushPromises, mount, type VueWrapper } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ref } from "vue";
import MovieFormModal from "../components/MovieFormModal.vue";
import TVShowFormModal from "../components/TVShowFormModal.vue";
import AnimeFormModal from "../components/AnimeFormModal.vue";
import { mapBackendMovieRaw } from "../services/movies";
import { mapBackendTVShowRaw } from "../services/tvShows";
import { mapBackendAnimeRaw } from "../services/anime";
import {
  followProviderId,
  providerIdsFromRows,
  trackingToInput,
  blankTracking,
} from "../utils/mediaEditorFields";

// A lookup that never finds anything unless a test selects a result.
const search = vi.hoisted(() => ({ current: null as unknown }));
vi.mock("../utils/useMetadataSearch", async () => {
  const { ref: r } = await import("vue");
  const selected = r<unknown>(null);
  search.current = selected;
  return {
    useMetadataSearch: () => ({
      selected,
      candidate: r(null),
      query: r(""),
      results: r([]),
      searching: r(false),
      enrichingMedia: r(false),
      message: r(null),
      warnings: r([]),
      previewArtwork: vi.fn(),
      run: vi.fn(),
      select: vi.fn(),
      applied: vi.fn(),
      stop: vi.fn(() => (selected.value = null)),
    }),
  };
});

const modalStub = { template: "<div><slot/><slot name='footer'/></div>" };
const wrappers: VueWrapper[] = [];
const requests: {
  url: string;
  method: string;
  body: Record<string, unknown>;
}[] = [];

// Echo the saved body back the way the API does, so the editor's `saved`
// event carries what a reload would show.
function stubApi(fixture: Record<string, unknown>) {
  requests.length = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const body = init?.body ? JSON.parse(String(init.body)) : {};
      requests.push({ url, method: init?.method ?? "GET", body });
      return new Response(JSON.stringify({ ...fixture, ...body }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }),
  );
}
afterEach(() => {
  wrappers.splice(0).forEach((wrapper) => wrapper.unmount());
  vi.unstubAllGlobals();
  (search.current as ReturnType<typeof ref>).value = null;
});
beforeEach(() => vi.stubEnv("VITE_USE_MOCK_DATA", "false"));

const common = {
  id: "media-1",
  user_id: "user-1",
  title: "Saved title",
  sort_title: "saved title",
  description: "Saved description",
  studios: ["Studio"],
  countries: ["US"],
  languages: ["en"],
  genres: ["Drama"],
  tags: ["keep"],
  features: ["HDR"],
  age_rating: "PG",
  source: "Blu-ray",
  provider_ids: { tmdb: "10" },
  poster_url: "https://img.test/poster.jpg",
  backdrop_url: null,
  status: "IN_PROGRESS",
  priority: "high",
  favorite: false,
  rewatches: 1,
  note: "Saved note",
  start_date: "2025-01-01",
  end_date: null,
  rating_story: 7,
  rating_performance: null,
  rating_soundtrack: null,
  rating_overall: 8,
  personal_rank: 3,
  locked_fields: ["title"],
  created_at: 1,
  updated_at: 2,
};
const season = {
  id: "season-1",
  show_id: "media-1",
  season_number: 1,
  name: null,
  episode_count: 12,
  episodes_watched: 3,
  status: "IN_PROGRESS",
  air_date: null,
  poster_url: null,
  episodes: [],
  created_at: 1,
  updated_at: 2,
};
const movieRaw = {
  ...common,
  release_date: "2020-05-01",
  runtime_minutes: 120,
  director: "Dir",
  writer: "Wri",
  tmdb_score: 7.5,
  progress_minutes: 42,
};
const tvRaw = {
  ...common,
  first_air_date: "2020-05-01",
  episode_runtime_minutes: 45,
  creators: ["Maker"],
  tmdb_score: 7.5,
  external_id: "99",
  provider_ids: { tmdb: "10", tvmaze: "99" },
  seasons: [season],
  is_airing: null,
  next_episode_air_at: null,
  next_episode_number: null,
  airing_interval_days: 14,
};
const animeRaw = {
  ...common,
  first_air_date: "2020-05-01",
  episode_runtime_minutes: 24,
  format: "TV",
  anilist_score: 8,
  mal_score: 7,
  external_id: "5",
  anilist_id: "6",
  provider_ids: { mal: "5", anilist: "6" },
  seasons: [season],
  kitsu_id: null,
  is_airing: null,
  next_episode_air_at: null,
  next_episode_number: null,
  airing_interval_days: null,
  linked_tv_show_id: "tv-9",
  linked_movie_id: null,
};

const kinds = [
  {
    name: "movie",
    component: MovieFormModal,
    prop: "movie",
    raw: movieRaw,
    entity: () => mapBackendMovieRaw(movieRaw as never),
    update: "/api/movie/update/media-1",
  },
  {
    name: "TV",
    component: TVShowFormModal,
    prop: "show",
    raw: tvRaw,
    entity: () => mapBackendTVShowRaw(tvRaw as never),
    update: "/api/tv/update/media-1",
  },
  {
    name: "anime",
    component: AnimeFormModal,
    prop: "show",
    raw: animeRaw,
    entity: () => mapBackendAnimeRaw(animeRaw as never),
    update: "/api/anime/update/media-1",
  },
];

function mountEditor(kind: (typeof kinds)[number], editing = true) {
  stubApi(kind.raw);
  const wrapper = mount(kind.component, {
    props: editing ? { [kind.prop]: kind.entity() } : {},
    global: { stubs: { UiModal: modalStub } },
  });
  wrappers.push(wrapper);
  return wrapper;
}
async function openTab(wrapper: VueWrapper, name: string) {
  await wrapper
    .findAll('[role="tab"]')
    .find((tab) => tab.text() === name)!
    .trigger("click");
}
function control(wrapper: VueWrapper, label: string) {
  const field = wrapper
    .findAll("label")
    .find((item) => item.get("span").text().startsWith(label));
  if (!field) throw new Error(`No field labelled ${label}`);
  return field.get("input, select, textarea");
}
async function save(wrapper: VueWrapper) {
  await wrapper.get("form").trigger("submit");
  await flushPromises();
}

describe.each(kinds)("$name editor", (kind) => {
  it("offers the same direct tabs with Save pinned on every one", async () => {
    const wrapper = mountEditor(kind);
    expect(wrapper.findAll('[role="tab"]').map((tab) => tab.text())).toEqual([
      "Basics",
      "Details",
      "Tracking",
      "Artwork",
      "Links",
    ]);
    for (const name of ["Details", "Tracking", "Artwork", "Links"]) {
      await openTab(wrapper, name);
      expect(wrapper.get("button.primary-button").text()).toBe("Save Changes");
    }
  });

  it("round-trips the shared tracking and classification fields through the update request", async () => {
    const wrapper = mountEditor(kind);
    await control(wrapper, "Status").setValue("completed");
    await control(wrapper, "Favorite").setValue(true);
    await openTab(wrapper, "Details");
    await control(wrapper, "Countries").setValue("JP, KR");
    await control(wrapper, "Languages").setValue("ja");
    await control(wrapper, "Age rating").setValue("TV-MA");
    await control(wrapper, "Features").setValue("Dolby, Subtitles");
    await control(wrapper, "Genres").setValue("Action, Mystery");
    await openTab(wrapper, "Tracking");
    await control(wrapper, "Started").setValue("2025-02-01");
    await control(wrapper, "Finished").setValue("2025-03-01");
    await control(wrapper, "Rewatches").setValue("4");
    await control(wrapper, "Source").setValue("Netflix");
    await control(wrapper, "Overall").setValue("9.5");
    await control(wrapper, "Personal rank").setValue("2");
    await control(wrapper, "Story").setValue("8");
    await control(wrapper, "Performance").setValue("6.5");
    await control(wrapper, "Soundtrack").setValue("10");
    await control(wrapper, "Tags").setValue("rewatch, comfort");
    await control(wrapper, "Notes").setValue("  Great  ");
    await save(wrapper);

    expect(requests).toHaveLength(1);
    expect(requests[0]!.url).toBe(kind.update);
    expect(requests[0]!.body).toMatchObject({
      status: "WATCHED",
      favorite: true,
      countries: ["JP", "KR"],
      languages: ["ja"],
      age_rating: "TV-MA",
      features: ["Dolby", "Subtitles"],
      genres: ["Action", "Mystery"],
      start_date: "2025-02-01",
      end_date: "2025-03-01",
      rewatches: 4,
      source: "Netflix",
      rating_overall: 9.5,
      personal_rank: 2,
      rating_story: 8,
      rating_performance: 6.5,
      rating_soundtrack: 10,
      tags: ["rewatch", "comfort"],
      note: "Great",
      // not edited here, so the saved values travel unchanged
      priority: "high",
      provider_ids: kind.raw.provider_ids,
    });
    const [saved] = wrapper.emitted("saved")![0] as [Record<string, unknown>];
    expect(saved).toMatchObject({
      rewatches: 4,
      startDate: "2025-02-01",
      ageRating: "TV-MA",
      countries: ["JP", "KR"],
      features: ["Dolby", "Subtitles"],
      ratingSoundtrack: 10,
    });
  });

  it("loads every saved value into its field", async () => {
    const wrapper = mountEditor(kind);
    await openTab(wrapper, "Details");
    expect(
      (control(wrapper, "Countries").element as HTMLInputElement).value,
    ).toBe("US");
    expect(
      (control(wrapper, "Age rating").element as HTMLInputElement).value,
    ).toBe("PG");
    await openTab(wrapper, "Tracking");
    expect(
      (control(wrapper, "Started").element as HTMLInputElement).value,
    ).toBe("2025-01-01");
    expect(
      (control(wrapper, "Rewatches").element as HTMLInputElement).value,
    ).toBe("1");
    expect((control(wrapper, "Source").element as HTMLInputElement).value).toBe(
      "Blu-ray",
    );
    expect(
      (control(wrapper, "Notes").element as HTMLTextAreaElement).value,
    ).toBe("Saved note");
  });

  it("keeps immutable identity, the title lock and other seasons data on a plain save", async () => {
    const wrapper = mountEditor(kind);
    expect(
      (control(wrapper, "Protect title").element as HTMLInputElement).checked,
    ).toBe(true);
    await save(wrapper);
    const body = requests[0]!.body;
    expect(body.title).toBe("Saved title");
    // protected because the saved row locks its title; only a change is sent
    expect(body.title_lock).not.toBe(false);
    expect(body.provider_ids).toEqual(kind.raw.provider_ids);
    expect(body.priority).toBe("high");
    // saving the editor never rewrites the seasons or their progress
    expect(body).not.toHaveProperty("seasons");
    if (kind.name === "anime") {
      expect(body.linked_tv_show_id).toBe("tv-9");
      expect(body.airing_interval_days).toBeNull();
    }
    if (kind.name === "TV") expect(body.airing_interval_days).toBe(14);
  });

  it("exposes the title protection switch only for saved items", async () => {
    const editing = mountEditor(kind);
    expect(editing.text()).toContain("Protect title from metadata updates");
    const adding = mountEditor(kind, false);
    expect(adding.text()).not.toContain("Protect title from metadata updates");
  });

  it("edits provider identities and rejects an invalid one before saving", async () => {
    const wrapper = mountEditor(kind);
    await openTab(wrapper, "Links");
    await wrapper
      .findAll("button")
      .find((b) => b.text() === "Add provider ID")!
      .trigger("click");
    const rows = wrapper.findAll(".provider-row");
    const inputs = rows[rows.length - 1]!.findAll("input");
    await inputs[0]!.setValue("Bad Key");
    await inputs[1]!.setValue("1");
    await openTab(wrapper, "Basics");
    await save(wrapper);
    expect(requests).toHaveLength(0);
    // jumps to the tab holding the problem and explains it
    expect(wrapper.get('[role="tab"][aria-selected="true"]').text()).toBe(
      "Links",
    );
    expect(wrapper.get('[role="alert"]').text()).toContain(
      "not a valid provider name",
    );

    const retry = wrapper.findAll(".provider-row");
    await retry[retry.length - 1]!.findAll("input")[0]!.setValue("trakt");
    await save(wrapper);
    expect(requests).toHaveLength(1);
    expect(requests[0]!.body.provider_ids).toEqual({
      ...kind.raw.provider_ids,
      trakt: "1",
    });
  });

  it("refuses a finish date before the start date", async () => {
    const wrapper = mountEditor(kind);
    await openTab(wrapper, "Tracking");
    await control(wrapper, "Finished").setValue("2024-12-31");
    await save(wrapper);
    expect(requests).toHaveLength(0);
    expect(wrapper.get('[role="alert"]').text()).toContain(
      "on or after the start date",
    );
  });

  it("reaches the staged lookup from every tab and saves nothing until it is applied", async () => {
    const wrapper = mountEditor(kind);
    await openTab(wrapper, "Tracking");
    (search.current as ReturnType<typeof ref>).value = {
      candidateId: "one",
      provider: "Provider",
      providerId: "42",
      providerIds: { tmdb: "42" },
      title: "Offered title",
      description: "Offered description",
      genres: ["Offered"],
      countries: ["FR"],
      languages: ["fr"],
      studios: [],
      creators: [],
      seasons: [],
      format: "Movie",
    };
    await flushPromises();
    expect(wrapper.text()).toContain("Offered");
    expect(requests).toHaveLength(0);
    // Save waits for the choice
    expect(
      wrapper.get("button.primary-button").attributes("disabled"),
    ).toBeDefined();
    expect(requests).toHaveLength(0);
  });
});

describe("shared editor helpers", () => {
  it("validates provider identities like the API does", () => {
    expect(
      providerIdsFromRows([
        { key: " TMDB ", value: " 5 " },
        { key: "", value: "" },
      ]),
    ).toEqual({
      ids: { tmdb: "5" },
      error: null,
    });
    expect(providerIdsFromRows([{ key: "a b", value: "1" }]).error).toMatch(
      /valid provider name/,
    );
    expect(providerIdsFromRows([{ key: "tmdb", value: "" }]).error).toMatch(
      /Enter the ID/,
    );
    expect(
      providerIdsFromRows([{ key: "tmdb", value: "x".repeat(257) }]).error,
    ).toMatch(/256/);
    expect(
      providerIdsFromRows([
        { key: "tmdb", value: "1" },
        { key: "TMDB", value: "2" },
      ]).error,
    ).toMatch(/more than once/);
    expect(
      providerIdsFromRows(
        Array.from({ length: 33 }, (_, i) => ({ key: `p${i}`, value: "1" })),
      ).error,
    ).toMatch(/at most 32/);
  });

  it("follows an edited identity into the legacy column", () => {
    expect(followProviderId("1", "1", "2")).toBe("2");
    expect(followProviderId("1", "1", undefined)).toBeNull();
    expect(followProviderId("legacy", undefined, undefined)).toBe("legacy");
    expect(followProviderId(null, undefined, "7")).toBe("7");
  });

  it("validates tracking numbers and keeps real zero", () => {
    const fields = { ...blankTracking(), ratingOverall: 0, rewatches: 0 };
    expect(trackingToInput(fields)).toMatchObject({
      ratingOverall: 0,
      rewatches: 0,
    });
    expect(() => trackingToInput({ ...fields, rewatches: 1.5 })).toThrow(
      "whole number",
    );
    expect(() => trackingToInput({ ...fields, ratingStory: 11 })).toThrow(
      "10 or less",
    );
    expect(() =>
      trackingToInput({ ...fields, source: "x".repeat(51) }),
    ).toThrow("50 characters");
  });
});
