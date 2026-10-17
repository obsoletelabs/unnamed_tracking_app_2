// @vitest-environment happy-dom
import { flushPromises, mount, type VueWrapper } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import GameFormModal from "../components/GameFormModal.vue";
import GameQuickEditModal from "../components/game/GameQuickEditModal.vue";
import MediaLibraryView from "../components/library/MediaLibraryView.vue";
import { mockGames } from "../data/mockGames";
import { tasks } from "../state/taskProgress";
import type { Game } from "../types/game";
import type { GameEditDraft } from "../utils/gameEditDraft";
import type { LibraryCardVM } from "../types/mediaLibrary";
import { mediaTrackingFacts } from "../utils/mediaTrackingFacts";
import { resolveStatus } from "../utils/mediaStatus";

const services = vi.hoisted(() => ({
  createGame: vi.fn(),
  updateGame: vi.fn(),
  fetchGames: vi.fn(),
  uploadGameAsset: vi.fn(),
  attachGameAssetFromUrl: vi.fn(),
  denormalizeStatus: vi.fn(),
  mapBackendGame: vi.fn(),
}));
const quick = vi.hoisted(() => ({ quickUpdateGame: vi.fn() }));
vi.mock("../services/games", () => services);
vi.mock("../services/gameQuickEdit", () => quick);
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
      enrichingMedia: ref(false),
      warnings: ref([]),
      error: ref(null),
      manualSearchHint: ref(null),
      hasSearched: ref(false),
      previewArtwork: vi.fn(),
      stop: vi.fn(),
    }),
  };
});

vi.mock("vue-router", () => ({
  useRoute: () => ({ path: "/movies", query: {} }),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}));
const modalStub = {
  template:
    "<div><slot name='header' :titleId=\"'dialog-title'\"/><slot/><slot name='footer'/></div>",
};
const wrappers: VueWrapper[] = [];
beforeEach(() => {
  tasks.splice(0);
  vi.stubEnv("VITE_USE_MOCK_DATA", "false");
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(new Response(JSON.stringify({ codes: [] }))),
  );
  let urls = 0;
  Object.defineProperty(URL, "createObjectURL", {
    configurable: true,
    writable: true,
    value: vi.fn(() => `blob:preview-${++urls}`),
  });
  Object.defineProperty(URL, "revokeObjectURL", {
    configurable: true,
    writable: true,
    value: vi.fn(),
  });
  services.fetchGames.mockReset().mockResolvedValue([]);
  services.updateGame
    .mockReset()
    .mockImplementation(async (id, input) => ({ ...game(), id, ...input }));
  services.uploadGameAsset.mockReset().mockResolvedValue({});
  services.attachGameAssetFromUrl.mockReset().mockResolvedValue({});
  quick.quickUpdateGame
    .mockReset()
    .mockImplementation(async (_id, edit) => ({ ...game(), ...edit }));
});
afterEach(() => {
  wrappers.splice(0).forEach((wrapper) => wrapper.unmount());
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

function game(): Game {
  return {
    ...mockGames[0]!,
    id: "game-id",
    title: "Saved game",
    folderLocation: "Saved-game",
    status: "playing",
    platform: "PC",
    ratingOverall: 7,
    collections: [],
    lockedFields: [],
    coverImageUrl: "/api/game/game-id/assets/key_art?w=400",
  };
}
const global = {
  stubs: { UiModal: modalStub, RouterLink: true, PageSettingsEditor: true },
};
function mountQuick(draft: GameEditDraft | null = null) {
  const wrapper = mount(GameQuickEditModal, {
    props: { game: game(), draft },
    attachTo: document.body,
    global,
  });
  wrappers.push(wrapper);
  return wrapper;
}
const button = (wrapper: VueWrapper, text: string) =>
  wrapper.findAll("button").find((item) => item.text() === text)!;
function field(wrapper: VueWrapper, label: string) {
  const match = wrapper
    .findAll("label.field")
    .find((item) => item.find("span").text().startsWith(label));
  expect(match, label).toBeDefined();
  return match!.find("input, select");
}
async function chooseFile(wrapper: VueWrapper, file: File) {
  const input = wrapper.get('input[type="file"]');
  Object.defineProperty(input.element, "files", {
    configurable: true,
    value: [file],
  });
  await input.trigger("change");
}
const submit = async (wrapper: VueWrapper) => {
  await wrapper.get("form").trigger("submit");
  await flushPromises();
};

describe("game quick editor", () => {
  it("saves only status, platform and rating, and labels the saved cover Current", async () => {
    const wrapper = mountQuick();
    expect(wrapper.get(".art-tag").text()).toBe("Current");
    expect(button(wrapper, "Save").attributes("disabled")).toBeDefined();
    await field(wrapper, "Status").setValue("beaten");
    await field(wrapper, "Platform").setValue("Switch");
    await field(wrapper, "Your rating").setValue("9.5");
    await submit(wrapper);
    expect(quick.quickUpdateGame).toHaveBeenCalledWith("game-id", {
      status: "beaten",
      platform: "Switch",
      ratingOverall: 9.5,
    });
    expect(services.updateGame).not.toHaveBeenCalled();
    expect(wrapper.emitted("saved")).toHaveLength(1);
  });

  it("rejects an out-of-range rating without saving and recovers after a failed save", async () => {
    const wrapper = mountQuick();
    await field(wrapper, "Your rating").setValue("11");
    await submit(wrapper);
    expect(wrapper.get('[role="alert"]').text()).toBe(
      "Your rating: enter 10 or less.",
    );
    expect(quick.quickUpdateGame).not.toHaveBeenCalled();

    await field(wrapper, "Your rating").setValue("8");
    quick.quickUpdateGame.mockRejectedValueOnce(new Error("Server said no"));
    await submit(wrapper);
    expect(wrapper.get('[role="alert"]').text()).toBe("Server said no");
    expect(wrapper.emitted("saved")).toBeUndefined();
    await submit(wrapper);
    expect(wrapper.emitted("saved")).toHaveLength(1);
  });

  it("uploads a chosen cover after saving and keeps the game if it fails", async () => {
    const wrapper = mountQuick();
    await chooseFile(wrapper, new File(["x"], "cover.png"));
    expect(wrapper.get(".art-tag").text()).toBe("Local file");
    services.uploadGameAsset.mockRejectedValueOnce(new Error("disk full"));
    await submit(wrapper);
    expect(quick.quickUpdateGame).toHaveBeenCalledTimes(1);
    expect(services.uploadGameAsset).toHaveBeenCalledWith(
      "game-id",
      "key_art",
      expect.any(File),
    );
    expect(wrapper.emitted("saved")).toHaveLength(1);
    expect(tasks.at(-1)?.detail).toMatch(/Game saved\. Artwork could not/);
  });

  it("hands unsaved values and the chosen artwork to the full editor", async () => {
    const quickEditor = mountQuick();
    await field(quickEditor, "Status").setValue("dropped");
    await field(quickEditor, "Platform").setValue("Amiga");
    await field(quickEditor, "Your rating").setValue("3.5");
    const file = new File(["x"], "cover.png");
    await chooseFile(quickEditor, file);
    await button(quickEditor, "Edit more").trigger("click");
    const draft = quickEditor.emitted("more")![0]![0] as GameEditDraft;
    expect(draft).toMatchObject({
      status: "dropped",
      platform: "Amiga",
      ratingOverall: 3.5,
      cover: { source: "file", file },
    });
    expect(quick.quickUpdateGame).not.toHaveBeenCalled();

    const full = mount(GameFormModal, {
      props: { game: game(), draft },
      attachTo: document.body,
      global,
    });
    wrappers.push(full);
    expect((field(full, "Status").element as HTMLSelectElement).value).toBe(
      "dropped",
    );
    expect((field(full, "Platform").element as HTMLInputElement).value).toBe(
      "Amiga",
    );
    await full
      .findAll('[role="tab"]')
      .find((tab) => tab.text() === "Ratings and tags")!
      .trigger("click");
    expect((field(full, "Atmosphere").element as HTMLInputElement).value).toBe(
      "3.5",
    );
    await submit(full);
    expect(services.updateGame).toHaveBeenCalledWith(
      "game-id",
      expect.objectContaining({
        status: "dropped",
        platform: "Amiga",
        ratingOverall: 3.5,
      }),
    );
    expect(services.uploadGameAsset).toHaveBeenCalledWith(
      "game-id",
      "key_art",
      file,
    );
  });

  it("opens the full editor on the saved values when there is no draft", () => {
    const full = mount(GameFormModal, {
      props: { game: game() },
      attachTo: document.body,
      global,
    });
    wrappers.push(full);
    expect((field(full, "Status").element as HTMLSelectElement).value).toBe(
      "playing",
    );
  });
});

function card(status: string, extra: Partial<LibraryCardVM> = {}) {
  const item: LibraryCardVM = {
    id: "m1",
    title: "A movie",
    poster: null,
    status,
    favorite: false,
    score: 8,
    personalRank: null,
    note: null,
    genres: [],
    isEpisodic: false,
    watched: 0,
    total: null,
    progressLabel: "",
    canAdvance: false,
    releaseYear: null,
    startDate: "2026-01-02",
    endDate: null,
    ...extra,
  };
  return item;
}
async function mountLibrary(item: LibraryCardVM) {
  const wrapper = mount(MediaLibraryView, {
    props: {
      kind: "movie",
      addLabel: "Add",
      items: [item],
      total: 1,
      statusCounts: {},
      loading: false,
      error: null,
      detailRoute: (id: string) => `/movies/${id}`,
    },
    global: {
      stubs: { UiModal: modalStub, RouterLink: true, MediaTopBar: true },
    },
  });
  wrappers.push(wrapper);
  await wrapper.get('button[title="Edit"]').trigger("click");
  return wrapper;
}
const saveEdit = async (wrapper: VueWrapper) => {
  await button(wrapper, "Save").trigger("click");
  return wrapper.emitted("save-edit");
};
const qaField = (wrapper: VueWrapper, label: string) =>
  wrapper
    .findAll(".qa-field")
    .find((item) => item.find("span").text() === label)!
    .find("input, select");

describe("media quick editor", () => {
  it.each([
    ["favorite", "completed"],
    ["rewatch", "watching"],
  ])(
    "keeps %s when the visible %s bucket is unchanged",
    async (real, bucket) => {
      const wrapper = await mountLibrary(card(real));
      expect(
        (qaField(wrapper, "Status").element as HTMLSelectElement).value,
      ).toBe(bucket);
      await qaField(wrapper, "Your rating (0–10)").setValue("9");
      const saved = await saveEdit(wrapper);
      expect(saved![0]![1]).toMatchObject({ status: real, score: 9 });
    },
  );

  it("writes the canonical status once the bucket actually changes", async () => {
    const wrapper = await mountLibrary(card("favorite"));
    await qaField(wrapper, "Status").setValue("watching");
    expect((await saveEdit(wrapper))![0]![1]).toMatchObject({
      status: "in progress",
    });
  });

  it("edits start and end dates and refuses a finish before the start", async () => {
    const wrapper = await mountLibrary(card("in progress"));
    expect(
      (qaField(wrapper, "Started").element as HTMLInputElement).value,
    ).toBe("2026-01-02");
    await qaField(wrapper, "Finished").setValue("2025-12-31");
    expect(await saveEdit(wrapper)).toBeUndefined();
    expect(wrapper.get('[role="alert"]').text()).toMatch(/on or after/);
    await qaField(wrapper, "Finished").setValue("2026-02-03");
    await qaField(wrapper, "Started").setValue("");
    expect((await saveEdit(wrapper))![0]![1]).toMatchObject({
      startDate: null,
      endDate: "2026-02-03",
    });
  });
});

describe("shared status and detail helpers", () => {
  it("resolveStatus keeps the stored value only inside the same bucket", () => {
    expect(resolveStatus("completed", "favorite")).toBe("favorite");
    expect(resolveStatus("watching", "rewatch")).toBe("rewatch");
    expect(resolveStatus("completed", "rewatch")).toBe("watched");
    expect(resolveStatus("plan")).toBe("wishlist");
  });

  it("lists priority and formatted dates, hiding priority once finished", () => {
    const media = {
      status: "watchlist",
      priority: "HIGH",
      startDate: "2026-09-12",
      endDate: "2026-10-01",
    };
    expect(mediaTrackingFacts(media)).toEqual([
      { label: "Priority", value: "1 · Highest" },
      { label: "Started", value: expect.stringContaining("2026") },
      { label: "Finished", value: expect.stringContaining("2026") },
    ]);
    expect(mediaTrackingFacts(media)[1]!.value).toContain("12");
    expect(
      mediaTrackingFacts({ ...media, status: "watched" }).map((f) => f.label),
    ).toEqual(["Started", "Finished"]);
    expect(
      mediaTrackingFacts({
        status: "wishlist",
        priority: null,
        startDate: null,
        endDate: null,
      }),
    ).toEqual([]);
  });
});
