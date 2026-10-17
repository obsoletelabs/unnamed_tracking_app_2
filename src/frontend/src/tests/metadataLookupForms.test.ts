// @vitest-environment happy-dom
import { flushPromises, mount, type VueWrapper } from "@vue/test-utils";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ref, nextTick } from "vue";
import MovieFormModal from "../components/MovieFormModal.vue";
import TVShowFormModal from "../components/TVShowFormModal.vue";
import AnimeFormModal from "../components/AnimeFormModal.vue";
import GameFormModal from "../components/GameFormModal.vue";
import type { MetadataCandidate } from "../services/metadata";

const saves = vi.hoisted(() => ({
  movie: vi.fn(),
  tv: vi.fn(),
  anime: vi.fn(),
  game: vi.fn(),
}));
vi.mock("../services/movies", () => ({ createMovie: saves.movie }));
vi.mock("../services/tvShows", () => ({ createTVShow: saves.tv }));
vi.mock("../services/anime", () => ({ createAnime: saves.anime }));
vi.mock("../services/games", () => ({ createGame: saves.game }));
vi.mock("../utils/useMetadataSearch", () => ({
  useMetadataSearch: () => search,
}));
vi.mock("../composables/useMetadataSearch", () => ({
  useMetadataSearch: () => progressive,
}));

function result() {
  return {
    candidateId: "one",
    providerIds: { tvmaze: "42", anilist: "12", mal: "13" },
    title: "Chosen title",
    provider: "Provider",
    providerId: "42",
    description: "Offered description",
    genres: ["Drama"],
    studios: ["Studio"],
    countries: ["US"],
    languages: ["en"],
    creators: ["Creator"],
    releaseDate: null,
    firstAirDate: null,
    runtimeMinutes: 90,
    episodeRuntimeMinutes: 24,
    director: null,
    writer: null,
    tmdbScore: 8,
    malScore: 7,
    anilistScore: 8,
    seasons: [
      {
        seasonNumber: 1,
        name: "Season 1",
        episodeCount: 10,
        airDate: null,
        posterUrl: null,
      },
    ],
  };
}
function makeSearch() {
  const selected = ref<ReturnType<typeof result> | null>(null);
  const candidate = ref<MetadataCandidate | null>(null);
  return {
    selected,
    candidate,
    query: ref(""),
    results: ref([]),
    searching: ref(false),
    enrichingMedia: ref(false),
    message: ref(null),
    warnings: ref([]),
    previewArtwork: vi.fn(),
    run: vi.fn(),
    select: vi.fn(),
    applied: vi.fn(),
    stop: vi.fn(() => {
      selected.value = null;
      candidate.value = null;
    }),
  };
}
function makeProgressive() {
  const selected = ref<MetadataCandidate | null>(null);
  return {
    selected,
    results: ref([]),
    searching: ref(false),
    enrichingMedia: ref(false),
    warnings: ref([]),
    error: ref(null),
    manualSearchHint: ref(null),
    hasSearched: ref(false),
    previewArtwork: vi.fn(),
    search: vi.fn(),
    select: vi.fn(),
    focus: vi.fn(),
    stop: vi.fn(() => {
      selected.value = null;
    }),
  };
}
let search = makeSearch();
let progressive = makeProgressive();
const wrappers: VueWrapper[] = [];
beforeEach(() => {
  search = makeSearch();
  progressive = makeProgressive();
  vi.stubEnv("VITE_USE_MOCK_DATA", "true");
  Object.values(saves).forEach((save) =>
    save.mockReset().mockResolvedValue({ id: "saved" }),
  );
});
afterEach(() => {
  wrappers.splice(0).forEach((wrapper) => wrapper.unmount());
  vi.unstubAllEnvs();
});
const shell = {
  template:
    '<div><slot name="lookup"/><slot/><button @click="$emit(\'save\')">Save form</button></div>',
  emits: ["save"],
};

it.each([
  { component: MovieFormModal, save: saves.movie, name: "movie" },
  { component: TVShowFormModal, save: saves.tv, name: "TV" },
  { component: AnimeFormModal, save: saves.anime, name: "anime" },
])(
  "stages $name fields and identity until Apply, then persists only on Save",
  async ({ component, save }) => {
    const wrapper = mount(component, {
      global: { stubs: { MediaFormShell: shell } },
    });
    wrappers.push(wrapper);
    search.selected.value = result();
    await nextTick();
    const title = wrapper
      .findAll("input")
      .find(
        (input) =>
          input.attributes("placeholder") !== undefined &&
          input.attributes("type") === "text",
      )!;
    expect((title.element as HTMLInputElement).value).toBe("");
    expect(save).not.toHaveBeenCalled();
    await wrapper
      .findAll("button")
      .find((button) => button.text() === "Save form")!
      .trigger("click");
    await flushPromises();
    expect(save).not.toHaveBeenCalled();
    const apply = wrapper
      .findAll("button")
      .find((button) => /^Apply \d/.test(button.text()))!;
    await apply.trigger("click");
    expect((title.element as HTMLInputElement).value).toBe("Chosen title");
    expect(save).not.toHaveBeenCalled();
    await wrapper
      .findAll("button")
      .find((button) => button.text() === "Save form")!
      .trigger("click");
    await flushPromises();
    expect(save).toHaveBeenCalledOnce();
    expect(save.mock.calls[0]?.[0].providerIds).toEqual(result().providerIds);
  },
);

it("cancelling a media lookup discards offered text, identity and seasons", async () => {
  const wrapper = mount(TVShowFormModal, {
    global: { stubs: { MediaFormShell: shell } },
  });
  wrappers.push(wrapper);
  search.selected.value = result();
  await nextTick();
  await wrapper
    .findAll("button")
    .find((button) => button.text() === "Cancel lookup")!
    .trigger("click");
  expect(wrapper.text()).not.toContain("Season 1");
  expect(wrapper.find("textarea").element.value).toBe("");
  expect(saves.tv).not.toHaveBeenCalled();
});

it("keeps a game lookup unsaved and applies checked fields through the shared model", async () => {
  const wrapper = mount(GameFormModal, {
    props: { quickAdd: true },
    global: {
      stubs: {
        UiModal: { template: '<div><slot/><slot name="footer"/></div>' },
        RouterLink: true,
      },
    },
  });
  wrappers.push(wrapper);
  progressive.selected.value = {
    id: "one",
    rank: 0,
    title: "Chosen title",
    external_id: "42",
    provider: "core.steam",
    provider_name: "Steam",
    providers: ["core.steam"],
    provider_ids: { steam: "42" },
    year: 2007,
    media_type: "game",
    alternate_titles: [],
    assets: [],
    metadata: { description: "Offered description" },
  };
  await nextTick();
  const title = wrapper
    .findAll("label.field")
    .find((label) => label.text() === "Title")!
    .get("input");
  expect((title.element as HTMLInputElement).value).toBe("");
  expect(saves.game).not.toHaveBeenCalled();
  await wrapper
    .findAll("button")
    .find((button) => /^Apply \d/.test(button.text()))!
    .trigger("click");
  expect((title.element as HTMLInputElement).value).toBe("Chosen title");
  expect(saves.game).not.toHaveBeenCalled();
  await wrapper.get("form").trigger("submit");
  await flushPromises();
  expect(saves.game).toHaveBeenCalledOnce();
  expect(saves.game.mock.calls[0]?.[0].providerIds).toEqual({ steam: "42" });
});
