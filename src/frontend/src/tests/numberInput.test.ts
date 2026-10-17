// @vitest-environment happy-dom
import { flushPromises, mount, type VueWrapper } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import MovieFormModal from "../components/MovieFormModal.vue";
import TVShowFormModal from "../components/TVShowFormModal.vue";
import AnimeFormModal from "../components/AnimeFormModal.vue";
import GameFormModal from "../components/GameFormModal.vue";
import MediaLibraryView from "../components/library/MediaLibraryView.vue";
import type { LibraryCardVM } from "../types/mediaLibrary";
import { updateGame } from "../services/games";
import { mockGames } from "../data/mockGames";
import { createMovie } from "../services/movies";
import { createTVShow } from "../services/tvShows";
import { createAnime } from "../services/anime";
import { numberOrNull, requiredNumber } from "../utils/numberInput";

vi.mock("../services/movies", () => ({
  createMovie: vi.fn(),
  updateMovie: vi.fn(),
  deleteMovie: vi.fn(),
  movieToInput: vi.fn(),
}));
vi.mock("../services/tvShows", () => ({
  createTVShow: vi.fn(),
  updateTVShow: vi.fn(),
  deleteTVShow: vi.fn(),
  tvShowToInput: vi.fn(),
}));
vi.mock("../services/anime", () => ({
  createAnime: vi.fn(),
  updateAnime: vi.fn(),
  deleteAnime: vi.fn(),
  animeToInput: vi.fn(),
}));
vi.mock("../components/MediaMetadataSearch.vue", () => ({
  default: { render: () => null },
}));
vi.mock("../services/games", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../services/games")>()),
  fetchGames: vi.fn().mockResolvedValue([]),
  updateGame: vi.fn().mockResolvedValue({ id: "game-id" }),
}));
vi.mock("../services/metadata", () => ({
  fetchMetadataProviders: vi.fn().mockResolvedValue([]),
}));
vi.mock("../composables/useMetadataSearch", async () => {
  const { ref } = await import("vue");
  return {
    useMetadataSearch: () => ({
      selected: ref(null),
      results: ref([]),
      searching: ref(false),
      warnings: ref([]),
      enrichingMedia: ref(false),
      error: ref(null),
      manualSearchHint: ref(null),
      hasSearched: ref(false),
      previewArtwork: vi.fn(),
      stop: vi.fn(),
    }),
  };
});
vi.mock("../utils/useMetadataSearch", async () => {
  const { ref } = await import("vue");
  return {
    useMetadataSearch: () => ({
      query: ref(""),
      results: ref([]),
      selected: ref(null),
      searching: ref(false),
      enrichingMedia: ref(false),
      message: ref(null),
      warnings: ref([]),
      run: vi.fn(),
      select: vi.fn(),
      applied: vi.fn(),
      candidate: ref(null),
      previewArtwork: vi.fn(),
      stop: vi.fn(),
    }),
  };
});
const wrappers: VueWrapper[] = [];
beforeEach(() => {
  vi.clearAllMocks();
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(new Response(JSON.stringify({ codes: [] }))),
  );
});
afterEach(() => {
  wrappers.splice(0).forEach((wrapper) => wrapper.unmount());
  vi.unstubAllGlobals();
});
vi.mock("vue-router", () => ({
  useRoute: () => ({ query: {} }),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}));
const modalStub = { template: "<div><slot/><slot name='footer'/></div>" };

describe("game editor", () => {
  it.each(["", "0"])(
    "normalizes ratings, duration and price together (%s)",
    async (value) => {
      const game = {
        ...mockGames[0]!,
        id: "game-id",
        title: "Game",
        status: "backlog" as const,
        folderLocation: "Game",
        tags: [],
        features: [],
        links: [],
        collections: [],
        ownership: { ...mockGames[0]!.ownership, priceCurrency: "USD" },
      };
      const wrapper = mount(GameFormModal, {
        props: { game },
        global: {
          stubs: {
            UiModal: modalStub,
            RouterLink: true,
            PageSettingsEditor: true,
          },
        },
      });
      wrappers.push(wrapper);
      const tab = async (name: string) => {
        const button = wrapper
          .findAll("button")
          .find((candidate) => candidate.text() === name);
        expect(button).toBeDefined();
        await button!.trigger("click");
      };
      const setField = async (name: string) => {
        const label = wrapper
          .findAll("label")
          .find((candidate) => candidate.find("span").text() === name);
        expect(label).toBeDefined();
        await label!.get("input").setValue(value);
      };
      await tab("Play");
      for (const name of [
        "Main story",
        "Main + extras",
        "Completionist",
        "All play styles",
      ])
        await setField(name);
      await tab("Ratings and tags");
      for (const name of ["Atmosphere", "Story", "Gameplay", "Sound"])
        await setField(name);
      await tab("Ownership");
      await setField("Price");
      await wrapper.get("form").trigger("submit");
      await flushPromises();
      const normalized = value === "" ? null : 0;
      expect(updateGame).toHaveBeenCalledWith(
        "game-id",
        expect.objectContaining({
          timeToBeatHours: normalized,
          timeToBeatExtrasHours: normalized,
          timeToBeatCompletionistHours: normalized,
          timeToBeatAllHours: normalized,
          ratingOverall: normalized,
          ratingStory: normalized,
          ratingGameplay: normalized,
          ratingSound: normalized,
          ownership: expect.objectContaining({
            price: normalized,
            priceCurrency: value === "" ? null : "USD",
          }),
        }),
      );
    },
  );
});

describe("shared quick editor", () => {
  it("keeps a missing episode count open, then preserves zero and cleared optional fields", async () => {
    const card: LibraryCardVM = {
      id: "show-id",
      title: "A show",
      poster: null,
      status: "in progress",
      favorite: false,
      score: 8,
      personalRank: null,
      note: null,
      genres: [],
      isEpisodic: true,
      watched: 2,
      total: 12,
      progressLabel: "2 / 12",
      canAdvance: true,
      releaseYear: null,
    };
    const wrapper = mount(MediaLibraryView, {
      props: {
        kind: "tv",
        addLabel: "Add TV",
        items: [card],
        total: 1,
        statusCounts: {},
        loading: false,
        error: null,
        detailRoute: (id) => `/tv/${id}`,
      },
      global: {
        stubs: { UiModal: modalStub, RouterLink: true, MediaTopBar: true },
      },
    });
    wrappers.push(wrapper);
    await wrapper.get('button[title="Edit"]').trigger("click");
    const inputs = wrapper.findAll('.qa-field input[type="number"]');
    expect(inputs).toHaveLength(3);
    for (const input of inputs) await input.setValue("");
    const save = () =>
      wrapper
        .findAll("button")
        .find((button) => button.text() === "Save")!
        .trigger("click");
    await save();
    expect(wrapper.emitted("save-edit")).toBeUndefined();
    expect(wrapper.get('[role="alert"]').text()).toBe(
      "Episodes watched: enter a number.",
    );
    await inputs[0]!.setValue("0");
    await save();
    expect(wrapper.emitted("save-edit")).toEqual([
      [
        "show-id",
        {
          status: "in progress",
          watched: 0,
          totalEpisodes: null,
          score: null,
          seen: true,
        },
      ],
    ]);
  });
});

describe("number input boundaries", () => {
  it.each([null, undefined, "", "   "])(
    "clears an optional %s without inventing zero",
    (value) => {
      expect(numberOrNull(value)).toBeNull();
      expect(() => requiredNumber(value, "Episodes watched")).toThrow(
        "Episodes watched: enter a number.",
      );
    },
  );
  it.each([NaN, Infinity, -Infinity, "NaN", "Infinity", "oops", false, {}])(
    "rejects invalid %s without silently clearing it",
    (value) => {
      expect(() => numberOrNull(value, "Rating")).toThrow(
        "Rating: enter a finite number.",
      );
    },
  );
  it("preserves real zero and unrelated decimal precision", () => {
    expect(numberOrNull(0)).toBe(0);
    expect(numberOrNull("0")).toBe(0);
    expect(numberOrNull("1.2345")).toBe(1.2345);
  });
});

describe.each([
  ["movie", MovieFormModal, createMovie],
  ["TV", TVShowFormModal, createTVShow],
  ["anime", AnimeFormModal, createAnime],
] as const)("%s editor", (_kind, component, create) => {
  async function editor() {
    const wrapper = mount(component, {
      global: { stubs: { UiModal: modalStub } },
    });
    wrappers.push(wrapper);
    await wrapper.get('input[type="text"]').setValue("Numeric preview");
    return wrapper;
  }
  function save(wrapper: VueWrapper) {
    return wrapper.get("button.ui-btn-primary").trigger("click");
  }
  it.each(["", "0"])(
    "submits cleared/zero fields as typed values (%s)",
    async (value) => {
      const wrapper = await editor();
      for (const input of wrapper.findAll('input[type="number"]'))
        await input.setValue(value);
      await save(wrapper);
      await flushPromises();
      expect(create).toHaveBeenCalledWith(
        expect.objectContaining({
          ratingOverall: value === "" ? null : 0,
          personalRank: value === "" ? null : 0,
        }),
      );
      expect(wrapper.emitted("saved")).toHaveLength(1);
    },
  );
  it("keeps invalid runtime edits open and explains how to fix them", async () => {
    const wrapper = await editor();
    await wrapper.get('input[type="number"]').setValue("1.5");
    await save(wrapper);
    await flushPromises();
    expect(create).not.toHaveBeenCalled();
    expect(wrapper.get('[role="alert"]').text()).toContain(
      "enter a whole number",
    );
    expect(
      wrapper.get("button.ui-btn-primary").attributes("disabled"),
    ).toBeUndefined();
    await wrapper.get('input[type="number"]').setValue("0");
    await save(wrapper);
    await flushPromises();
    expect(create).toHaveBeenCalledTimes(1);
  });
});
