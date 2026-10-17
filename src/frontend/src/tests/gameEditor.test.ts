// @vitest-environment happy-dom
import { flushPromises, mount, type VueWrapper } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { nextTick, ref } from "vue";
import GameFormModal from "../components/GameFormModal.vue";
import MetadataComparison from "../components/MetadataComparison.vue";
import { mockGames } from "../data/mockGames";
import type { MetadataCandidate } from "../services/metadata";
import { smartCollections } from "../state/smartCollections";
import { tasks } from "../state/taskProgress";
import type { Game } from "../types/game";

const services = vi.hoisted(() => ({
  createGame: vi.fn(),
  updateGame: vi.fn(),
  fetchGames: vi.fn(),
  uploadGameAsset: vi.fn(),
  attachGameAssetFromUrl: vi.fn(),
}));
vi.mock("../services/games", () => services);
vi.mock("../services/metadata", () => ({
  fetchMetadataProviders: vi.fn().mockResolvedValue([]),
}));
vi.mock("../composables/useMetadataSearch", () => ({
  useMetadataSearch: () => progressive,
}));

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
    previewArtwork: vi.fn().mockResolvedValue(new Blob(["image"])),
    search: vi.fn(),
    select: vi.fn(),
    focus: vi.fn(),
    stop: vi.fn(() => {
      selected.value = null;
    }),
  };
}
let progressive = makeProgressive();

function candidate(): MetadataCandidate {
  return {
    id: "candidate",
    rank: 0,
    title: "Offered title",
    external_id: "42",
    provider: "plugin.covers",
    provider_name: "Optional covers plugin",
    providers: ["plugin.covers"],
    provider_ids: { "plugin.covers": "42" },
    year: 2020,
    media_type: "game",
    alternate_titles: [],
    metadata: { description: "Offered description" },
    assets: [
      {
        kind: "key_art",
        url: "https://art.test/cover.png",
        width: null,
        height: null,
      },
      {
        kind: "key_art",
        url: "https://art.test/alt.png",
        width: null,
        height: null,
      },
    ],
  };
}

function savedGame(): Game {
  return {
    ...mockGames[0]!,
    id: "game-id",
    title: "Saved game",
    folderLocation: "Saved-game",
    status: "playing",
    favorite: false,
    collections: ["Couch", "Smart rule"],
    resumeNote: "Boss rush next",
    platforms: [
      {
        platform: "PC",
        playtimeMinutes: 100,
        completionPercent: null,
        lastPlayedAt: null,
      },
    ],
    parentGameId: "parent-id",
    relationshipType: "dlc",
    playCompatibility: {
      minPlayers: 1,
      maxPlayers: 4,
      durationMinutes: null,
      crossplay: true,
    },
    pageSettings: { tabs: { notes: false } } as Game["pageSettings"],
    lockedFields: ["title"],
    ownership: {
      format: "physical",
      purchaseDate: "2024-01-02",
      price: 20,
      priceCurrency: "EUR",
      condition: "Boxed",
    },
    coverImageUrl: "/api/game/game-id/assets/key_art?w=400",
    bannerImageUrl: "/api/game/game-id/assets/banner",
  };
}

let objectUrls = 0;
const wrappers: VueWrapper[] = [];
beforeEach(() => {
  progressive = makeProgressive();
  objectUrls = 0;
  tasks.splice(0);
  vi.stubEnv("VITE_USE_MOCK_DATA", "false");
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(new Response(JSON.stringify({ codes: [] }))),
  );
  Object.defineProperty(URL, "createObjectURL", {
    configurable: true,
    writable: true,
    value: vi.fn(() => `blob:preview-${++objectUrls}`),
  });
  Object.defineProperty(URL, "revokeObjectURL", {
    configurable: true,
    writable: true,
    value: vi.fn(),
  });
  services.createGame.mockReset().mockImplementation(async (input) => ({
    ...savedGame(),
    id: "created-id",
    title: input.title,
  }));
  services.updateGame.mockReset().mockImplementation(async (id, input) => ({
    ...savedGame(),
    id,
    title: input.title,
  }));
  services.fetchGames
    .mockReset()
    .mockResolvedValue([
      savedGame(),
      { ...savedGame(), id: "other", collections: ["Backlog 2026"] },
    ]);
  services.uploadGameAsset.mockReset().mockResolvedValue({});
  services.attachGameAssetFromUrl.mockReset().mockResolvedValue({});
});
afterEach(() => {
  smartCollections.value = [];
  wrappers.splice(0).forEach((wrapper) => wrapper.unmount());
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

function open(game: Game | null = null) {
  const wrapper = mount(GameFormModal, {
    props: { game },
    attachTo: document.body,
    global: {
      stubs: {
        UiModal: {
          template:
            "<div><slot name='header' :titleId=\"'dialog-title'\"/><slot/><slot name='footer'/></div>",
        },
        RouterLink: true,
        PageSettingsEditor: true,
      },
    },
  });
  wrappers.push(wrapper);
  return wrapper;
}
const tabs = (wrapper: VueWrapper) =>
  wrapper.findAll('[role="tab"]').map((tab) => tab.text());
async function tab(wrapper: VueWrapper, name: string) {
  await wrapper
    .findAll('[role="tab"]')
    .find((item) => item.text() === name)!
    .trigger("click");
}
function field(wrapper: VueWrapper, label: string) {
  const match = wrapper
    .findAll("label.field")
    .find((item) => item.find("span").text() === label);
  expect(match, label).toBeDefined();
  return match!.find("input, textarea, select");
}
async function chooseFile(wrapper: VueWrapper, index: number, file: File) {
  const input = wrapper.findAll('input[type="file"]')[index]!;
  await input.trigger("click");
  expect((input.element as HTMLInputElement).value).toBe("");
  Object.defineProperty(input.element, "files", {
    configurable: true,
    value: [file],
  });
  await input.trigger("change");
}
const button = (wrapper: VueWrapper, text: string | RegExp) =>
  wrapper
    .findAll("button")
    .find((item) =>
      typeof text === "string" ? item.text() === text : text.test(item.text()),
    )!;
const submit = async (wrapper: VueWrapper) => {
  await wrapper.get("form").trigger("submit");
  await flushPromises();
};

describe("shared full game editor", () => {
  it("adds and edits through eight direct tabs with Save always available", async () => {
    const expected = [
      "Basics",
      "Details",
      "Play",
      "Ratings and tags",
      "Artwork",
      "Links",
      "Ownership",
      "Advanced",
    ];
    for (const game of [null, savedGame()]) {
      const wrapper = open(game);
      expect(tabs(wrapper)).toEqual(expected);
      for (const name of expected) {
        await tab(wrapper, name);
        const submitButton = wrapper.get('button[type="submit"]');
        expect(submitButton.text()).toBe(game ? "Save changes" : "Add game");
        expect(submitButton.attributes("form")).toBe(
          wrapper.get("form").attributes("id"),
        );
      }
      expect(wrapper.text()).not.toMatch(/Step \d|Next|Skip/);
    }
  });

  it("shows the hero title and moves through tabs with the keyboard", async () => {
    const wrapper = open(savedGame());
    expect(wrapper.get("#dialog-title").text()).toBe("Edit game");
    expect(wrapper.get(".editor-heading").text()).toBe("Saved game");
    await field(wrapper, "Title").setValue("Renamed");
    expect(wrapper.get(".editor-heading").text()).toBe("Renamed");
    const tablist = wrapper.get('[role="tablist"]');
    await tablist.trigger("keydown", { key: "ArrowLeft" });
    expect(wrapper.get('[aria-selected="true"]').text()).toBe("Advanced");
    await tablist.trigger("keydown", { key: "Home" });
    expect(wrapper.get('[aria-selected="true"]').text()).toBe("Basics");
    await tablist.trigger("keydown", { key: "ArrowRight" });
    expect(wrapper.get('[aria-selected="true"]').text()).toBe("Details");
    expect(wrapper.get('[role="tabpanel"]').attributes("aria-labelledby")).toBe(
      wrapper.get('[aria-selected="true"]').attributes("id"),
    );
    await wrapper.get(".editor-cover").trigger("click");
    expect(wrapper.get('[aria-selected="true"]').text()).toBe("Artwork");
  });

  it("keeps pending edits and files across tabs and creates one game", async () => {
    const wrapper = open();
    await field(wrapper, "Title").setValue("Tabbed game");
    await field(wrapper, "Title").trigger("blur");
    await tab(wrapper, "Artwork");
    const art = new File(["png"], "cover.png", { type: "image/png" });
    await chooseFile(wrapper, 0, art);
    const fileInput = wrapper.find('input[type="file"]').element;
    await tab(wrapper, "Links");
    await button(wrapper, "+ Add Link").trigger("click");
    await field(wrapper, "Label").setValue("Store");
    await field(wrapper, "URL").setValue("https://store.test");
    await tab(wrapper, "Play");
    await field(wrapper, "Playtime (hours)").setValue("2.5");
    await field(wrapper, "Where you left off").setValue("Second island");
    await tab(wrapper, "Basics");
    expect((field(wrapper, "Title").element as HTMLInputElement).value).toBe(
      "Tabbed game",
    );
    await tab(wrapper, "Artwork");
    // built once, then kept: the same input and its staged preview remain
    expect(wrapper.find('input[type="file"]').element).toBe(fileInput);
    expect(wrapper.text()).toContain("cover.png uploads when you save.");
    expect(wrapper.get(".editor-cover img").attributes("src")).toBe(
      "blob:preview-1",
    );

    wrapper.get("form").trigger("submit");
    wrapper.get("form").trigger("submit");
    await flushPromises();
    expect(services.createGame).toHaveBeenCalledOnce();
    expect(services.createGame.mock.calls[0]![0]).toMatchObject({
      title: "Tabbed game",
      folderLocation: "Tabbed-game",
      links: [{ label: "Store", url: "https://store.test" }],
      playtimeSeconds: 9000,
      resumeNote: "Second island",
    });
    expect(services.uploadGameAsset).toHaveBeenCalledWith(
      "created-id",
      "key_art",
      art,
    );
    expect(wrapper.emitted("saved")).toEqual([["created-id"]]);

    // a further Save from the same editor updates that game, never re-creates it
    await submit(wrapper);
    expect(services.createGame).toHaveBeenCalledOnce();
    expect(services.updateGame).toHaveBeenCalledWith(
      "created-id",
      expect.objectContaining({ title: "Tabbed game" }),
    );
  });

  it("builds the artwork tab lazily", async () => {
    const wrapper = open(savedGame());
    expect(wrapper.find('input[type="file"]').exists()).toBe(false);
    await tab(wrapper, "Artwork");
    expect(wrapper.findAll('input[type="file"]')).toHaveLength(2);
    await tab(wrapper, "Details");
    expect(wrapper.findAll('input[type="file"]')).toHaveLength(2);
    expect(
      wrapper.find('input[type="file"]').element.closest(".tab-panel"),
    ).toHaveProperty("style.display", "none");
  });

  it("keeps an explicit local file over offered provider artwork", async () => {
    const wrapper = open(savedGame());
    await tab(wrapper, "Artwork");
    const art = new File(["png"], "mine.png", { type: "image/png" });
    await chooseFile(wrapper, 0, art);

    progressive.selected.value = candidate();
    await flushPromises();
    await nextTick();
    // the verified offer is listed but not ticked over the chosen file
    const cover = wrapper
      .findAllComponents(MetadataComparison)[0]!
      .props("changes")
      .find((change) => change.key === "key_art");
    expect(cover).toMatchObject({ checked: false, current: "blob:preview-1" });
    await button(wrapper, /^Apply \d/).trigger("click");
    expect(wrapper.text()).toContain("mine.png uploads when you save.");
    expect(wrapper.findAll(".art-tile")).toHaveLength(3);

    await submit(wrapper);
    expect(services.uploadGameAsset).toHaveBeenCalledWith(
      "game-id",
      "key_art",
      art,
    );
    expect(services.attachGameAssetFromUrl).not.toHaveBeenCalled();
    expect(URL.revokeObjectURL).not.toHaveBeenCalledWith("blob:preview-1");
  });

  it("replaces a file only by an explicit choice and releases its preview", async () => {
    const wrapper = open(savedGame());
    await tab(wrapper, "Artwork");
    const first = new File(["a"], "first.png", { type: "image/png" });
    await chooseFile(wrapper, 0, first);
    // choosing the same file again still restages it with a fresh preview
    await chooseFile(wrapper, 0, first);
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:preview-1");
    expect(wrapper.text()).toContain("first.png uploads when you save.");

    await wrapper
      .get('input[aria-label="Cover image link"]')
      .setValue("https://example.test/cover.png");
    await button(wrapper, "Use").trigger("click");
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:preview-2");
    expect(wrapper.text()).toContain("This link is fetched when you save.");

    await chooseFile(wrapper, 1, first);
    wrapper.unmount();
    wrappers.splice(wrappers.indexOf(wrapper), 1);
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:preview-3");
  });

  it("reports artwork failure as a saved game with a retry that never re-creates", async () => {
    services.uploadGameAsset
      .mockRejectedValueOnce(new Error("Synthetic outage"))
      .mockResolvedValue({});
    const wrapper = open();
    await field(wrapper, "Title").setValue("Retry game");
    await field(wrapper, "Folder name").setValue("retry-game");
    await tab(wrapper, "Artwork");
    const art = new File(["png"], "retry.png", { type: "image/png" });
    await chooseFile(wrapper, 1, art);
    await submit(wrapper);

    expect(wrapper.emitted("saved")).toEqual([["created-id"]]);
    const task = tasks.find((item) => item.label === "Artwork for Retry game")!;
    expect(task).toMatchObject({ status: "error" });
    expect(task.detail).toMatch(
      /^Game saved\. Artwork could not be attached \(banner\)/,
    );
    wrapper.unmount();
    wrappers.splice(wrappers.indexOf(wrapper), 1);
    task.retry!();
    await flushPromises();
    expect(task.status).toBe("done");
    expect(services.uploadGameAsset).toHaveBeenCalledTimes(2);
    expect(services.uploadGameAsset).toHaveBeenLastCalledWith(
      "created-id",
      "banner",
      art,
    );
    expect(services.createGame).toHaveBeenCalledOnce();
  });

  it("saves favorite, manual collections, playtime and resume note with the game", async () => {
    // a membership shadowed by a smart rule is kept but never offered
    smartCollections.value = [
      { id: "rule", name: "Smart rule", field: "tag", value: "Co-op" },
    ];
    const wrapper = open(savedGame());
    await flushPromises();
    const favorite = wrapper
      .findAll("label.checkbox-field")
      .find((label) => label.text().startsWith("Favorite"))!;
    await favorite.get("input").setValue(true);
    await tab(wrapper, "Ratings and tags");
    const chips = () =>
      wrapper.findAll(".collection-choice").map((chip) => chip.text());
    expect(chips()).toEqual(["Backlog 2026", "Couch"]);
    await wrapper
      .findAll(".collection-choice")
      .find((chip) => chip.text() === "Couch")!
      .get("input")
      .setValue(false);
    await wrapper
      .get('input[aria-label="New collection name"]')
      .setValue("backlog 2026");
    await button(wrapper, "Add").trigger("click");
    await wrapper
      .get('input[aria-label="New collection name"]')
      .setValue("Replays");
    await button(wrapper, "Add").trigger("click");
    await tab(wrapper, "Play");
    expect(
      (field(wrapper, "Playtime (hours)").element as HTMLInputElement).value,
    ).toBe("1.7");
    await field(wrapper, "Where you left off").setValue("  ");
    await submit(wrapper);
    expect(services.updateGame).toHaveBeenCalledWith(
      "game-id",
      expect.objectContaining({
        favorite: true,
        collections: ["Smart rule", "Backlog 2026", "Replays"],
        resumeNote: null,
        // unchanged playtime is left alone rather than rounded
        playtimeSeconds: undefined,
      }),
    );

    await tab(wrapper, "Play");
    await field(wrapper, "Playtime (hours)").setValue("");
    await submit(wrapper);
    expect(services.updateGame.mock.lastCall![1].playtimeSeconds).toBe(0);
    await field(wrapper, "Playtime (hours)").setValue("999999");
    await submit(wrapper);
    expect(wrapper.get('[role="alert"]').text()).toBe(
      "Playtime: enter 596523 or less.",
    );
    expect(services.updateGame).toHaveBeenCalledTimes(2);
  });

  it("preserves multiplayer, relationships, ownership, page settings and title locks", async () => {
    const wrapper = open(savedGame());
    await flushPromises();
    await submit(wrapper);
    const input = services.updateGame.mock.lastCall![1];
    expect(input).toMatchObject({
      playCompatibility: savedGame().playCompatibility,
      parentGameId: "parent-id",
      relationshipType: "dlc",
      ownership: { ...savedGame().ownership, purchaseDate: "2024-01-02" },
      pageSettings: savedGame().pageSettings,
      providerIds: savedGame().providerIds,
    });
    // the existing protection is retained without an explicit override
    expect(input.titleLock).toBeUndefined();
    await tab(wrapper, "Advanced");
    expect(wrapper.text()).toContain("Related games");
    await tab(wrapper, "Play");
    expect(wrapper.text()).toContain("Multiplayer settings");
  });

  it("keeps an optional provider's lookup and offered artwork across tabs", async () => {
    const wrapper = open(savedGame());
    progressive.selected.value = candidate();
    await flushPromises();
    // the same mounted review (not a rebuilt one) stays on every tab
    const review = wrapper.findComponent(MetadataComparison).element;
    for (const name of ["Play", "Artwork", "Advanced", "Basics"]) {
      await tab(wrapper, name);
      expect(wrapper.findComponent(MetadataComparison).element).toBe(review);
    }
    expect(progressive.stop).not.toHaveBeenCalled();
    await button(wrapper, "Cancel lookup").trigger("click");
    await tab(wrapper, "Artwork");
    const offered = wrapper
      .findAll(".art-tile img")
      .map((image) => image.attributes("src"));
    expect(offered).toContain("https://art.test/alt.png");
    await wrapper
      .findAll(".art-tile")
      .find(
        (tile) =>
          tile.find("img").attributes("src") === "https://art.test/alt.png",
      )!
      .trigger("click");
    await submit(wrapper);
    expect(services.attachGameAssetFromUrl).toHaveBeenCalledWith(
      "game-id",
      "key_art",
      "https://art.test/alt.png",
    );
  });

  it("carries quick add edits into the tabbed full form", async () => {
    const wrapper = mount(GameFormModal, {
      props: { quickAdd: true },
      global: {
        stubs: {
          UiModal: { template: "<div><slot/><slot name='footer'/></div>" },
          RouterLink: true,
          PageSettingsEditor: true,
        },
      },
    });
    wrappers.push(wrapper);
    expect(wrapper.find('[role="tablist"]').exists()).toBe(false);
    await field(wrapper, "Title").setValue("Quick title");
    await field(wrapper, "Platform").setValue("Linux");
    await button(wrapper, "Full form").trigger("click");
    await flushPromises();
    expect(tabs(wrapper)).toHaveLength(8);
    expect(wrapper.get('[aria-selected="true"]').text()).toBe("Basics");
    expect((field(wrapper, "Title").element as HTMLInputElement).value).toBe(
      "Quick title",
    );
    expect((field(wrapper, "Platform").element as HTMLInputElement).value).toBe(
      "Linux",
    );
    expect(services.fetchGames).toHaveBeenCalledOnce();
    // a titled draft opens on its fields; the lookup stays one click away
    expect(wrapper.find('input[type="search"]').exists()).toBe(false);
  });

  it("opens the lookup from any tab of the full form", async () => {
    const wrapper = open(savedGame());
    expect(wrapper.find('input[type="search"]').exists()).toBe(false);
    await tab(wrapper, "Ownership");
    const toggle = button(wrapper, "Look up metadata");
    await toggle.trigger("click");
    expect(toggle.attributes("aria-expanded")).toBe("true");
    expect(wrapper.find('input[type="search"]').exists()).toBe(true);
    expect(wrapper.get('[aria-selected="true"]').text()).toBe("Ownership");
  });
});
