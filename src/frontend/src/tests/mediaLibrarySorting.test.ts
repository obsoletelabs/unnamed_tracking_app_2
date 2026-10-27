// @vitest-environment happy-dom
import { mount } from "@vue/test-utils";
import { afterEach, expect, it, vi } from "vitest";
import MediaLibraryView from "../components/library/MediaLibraryView.vue";
import { mediaBucketCounts } from "../utils/mediaStatus";
import { createMediaApi } from "../services/mediaApi";
import { createEntityCache } from "../utils/entityCache";
import { savedMediaSort, type LibraryFilters } from "../utils/libraryFilters";
import type { LibraryCardVM } from "../types/mediaLibrary";

vi.mock("vue-router", () => ({
  useRoute: () => ({ path: "/movies", query: {} }),
  useRouter: () => ({ push: vi.fn() }),
}));
afterEach(() => {
  localStorage.clear();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

it("sums every real status into the five visible buckets independently of a selected page", () => {
  expect(
    mediaBucketCounts({
      WATCHED: 4,
      FAVORITE: 3,
      REWATCH: 2,
      IN_PROGRESS: 1,
      BACKLOG: 1,
      WISHLIST: 2,
      WATCHLIST: 3,
      DROPPED: 1,
    }),
  ).toEqual({
    all: 17,
    completed: 7,
    watching: 3,
    hold: 1,
    plan: 5,
    dropped: 1,
  });
});

it("includes the selected sort in media requests and restores valid saved choices", async () => {
  const request = vi.fn<typeof fetch>(
    async () =>
      new Response(
        JSON.stringify({
          items: [],
          total: 0,
          offset: 0,
          limit: 100,
          status_counts: {},
          score_ranks: {},
        }),
      ),
  );
  vi.stubGlobal("fetch", request);
  const api = createMediaApi({
    base: "/api/movie",
    noun: "movie",
    plural: "movies",
    cache: createEntityCache<{ id: string }>(),
    map: (raw: { id: string }) => raw,
    toBody: (input: Record<string, never>) => input,
  });
  const filters: LibraryFilters = {
    search: "",
    genres: [],
    genreMatchAll: false,
    formats: [],
    onlyFavorites: false,
    onlyUnrated: false,
    onlyWithNote: false,
    minScore: null,
    yearFrom: "",
    yearTo: "",
    sort: "score",
  };
  await api.fetchPage(0, 100, filters);
  await api.fetchPage(0, 100, { ...filters, sort: "progress" });
  expect(
    request.mock.calls.map((call) =>
      new URL(String(call[0]), "http://test").searchParams.get("sort"),
    ),
  ).toEqual(["score", "progress"]);
  localStorage.setItem("librarySort:movie", "year_old");
  expect(savedMediaSort("movie")).toBe("year_old");
  localStorage.setItem("librarySort:movie", "invalid");
  expect(savedMediaSort("movie")).toBe("rank");
  const revision = api.mutationRevision();
  request.mockResolvedValueOnce(
    new Response(JSON.stringify({ id: "created" })),
  );
  await api.create({});
  expect(api.mutationRevision()).toBe(revision + 1);
});

it("exposes accessible sortable headers and preserves the server's global row order", async () => {
  vi.useFakeTimers();
  localStorage.setItem("libraryLayout", "list");
  const card: LibraryCardVM = {
    id: "1",
    title: "Zulu",
    poster: null,
    status: "watched",
    favorite: false,
    score: 9,
    personalRank: null,
    note: null,
    genres: [],
    isEpisodic: false,
    watched: 1,
    total: 1,
    progressLabel: "Seen",
    canAdvance: false,
    releaseYear: null,
  };
  const view = mount(MediaLibraryView, {
    props: {
      kind: "movie",
      addLabel: "Add movie",
      items: [card, { ...card, id: "2", title: "Alpha", score: 8 }],
      total: 7,
      statusCounts: { WATCHED: 4, FAVORITE: 3, WISHLIST: 10 },
      hasMore: false,
      scoreRanks: { "1": 1, "2": 2 },
      loading: false,
      error: null,
      detailRoute: (id) => `/movies/${id}`,
    },
    global: { stubs: { MediaTopBar: true, RouterLink: true } },
  });
  expect(view.findAll(".list-title").map((row) => row.text())).toEqual([
    "Zulu",
    "Alpha",
  ]);
  expect(
    view.findAll("button").find((button) => button.text() === "All 17"),
  ).toBeDefined();
  await view.get('button[aria-label="Sort by Title"]').trigger("click");
  await vi.advanceTimersByTimeAsync(250);
  expect(
    view.get('[role="columnheader"][aria-sort="ascending"]').text(),
  ).toContain("Title");
  expect(view.emitted("filters-change")!.at(-1)![0]).toMatchObject({
    sort: "title",
  });
  await view.get('button[aria-label="Sort by Title"]').trigger("click");
  await vi.advanceTimersByTimeAsync(250);
  expect(view.emitted("filters-change")!.at(-1)![0]).toMatchObject({
    sort: "title_desc",
  });
  expect(
    view.get('[role="columnheader"][aria-sort="descending"]').text(),
  ).toContain("Title");
  view.unmount();
});

it("loads more at the end of a board status row without blanking its current cards", async () => {
  localStorage.setItem("libraryLayout", "board");
  const card: LibraryCardVM = {
    id: "watched",
    title: "Seen",
    poster: null,
    status: "watched",
    favorite: false,
    score: null,
    personalRank: null,
    note: null,
    genres: [],
    isEpisodic: false,
    watched: 1,
    total: 1,
    progressLabel: "Seen",
    canAdvance: false,
    releaseYear: null,
  };
  const view = mount(MediaLibraryView, {
    props: {
      kind: "movie",
      addLabel: "Add movie",
      items: [
        card,
        { ...card, id: "planned", title: "Planned", status: "watchlist" },
      ],
      total: 20,
      statusCounts: { WATCHED: 10, WATCHLIST: 10 },
      hasMore: true,
      loading: false,
      error: null,
      detailRoute: (id) => `/movies/${id}`,
    },
    global: { stubs: { RouterLink: true, MediaTopBar: true, UiModal: true } },
  });
  const row = view
    .findAll(".board-section")
    .find((section) => section.text().includes("Seen"))!;
  await row.findAll(".board-nav button")[1]!.trigger("click");
  expect(view.emitted("load-more")).toHaveLength(1);
  expect(row.text()).toContain("Seen");
  view.unmount();
});
