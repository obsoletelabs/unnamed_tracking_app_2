// @vitest-environment happy-dom
import { flushPromises, mount, type VueWrapper } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useGameDetail } from "../composables/useGameDetail";
import { mockGames } from "../data/mockGames";
import * as games from "../services/games";
import * as media from "../services/media";
import * as archives from "../services/gameArchives";
import GameMediaPanel from "../components/GameMediaPanel.vue";
import GameArchivesPanel from "../components/GameArchivesPanel.vue";
import GameNotesPanel from "../components/GameNotesPanel.vue";

const { mainId, otherId } = vi.hoisted(() => ({
  mainId: "00000000-0000-4000-8000-000000000001",
  otherId: "00000000-0000-4000-8000-000000000002",
}));
vi.mock("../services/games");
vi.mock("../services/media");
vi.mock("../services/gameArchives");
vi.mock("vue-router", () => ({
  useRoute: () => ({ params: { id: mainId }, query: {} }),
  useRouter: () => ({ push: vi.fn() }),
}));

const wrappers: VueWrapper[] = [];
const game = { ...mockGames[0]!, id: mainId, title: "Minecraft" };

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

async function detail() {
  let model!: ReturnType<typeof useGameDetail>;
  wrappers.push(
    mount({
      setup() {
        model = useGameDetail();
        return () => null;
      },
    }),
  );
  model.game.value = game;
  await flushPromises();
  return model;
}

beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(games.fetchGames).mockResolvedValue([]);
  vi.mocked(games.fetchGame).mockResolvedValue(game);
  vi.mocked(games.fetchGameVariants).mockResolvedValue([]);
  vi.mocked(games.fetchGameAchievements).mockResolvedValue([]);
  vi.mocked(media.listGameFiles).mockResolvedValue([]);
  vi.mocked(media.fetchGameFileTrash).mockResolvedValue([]);
  vi.mocked(archives.fetchArchives).mockResolvedValue([]);
  vi.mocked(archives.fetchArchiveTrash).mockResolvedValue([]);
});
afterEach(() => wrappers.splice(0).forEach((wrapper) => wrapper.unmount()));

describe("game page loaders", () => {
  it("drops a media list that arrives after the page moved to another game", async () => {
    const model = await detail();
    const slow = deferred<media.MediaItem[]>();
    vi.mocked(media.listGameScreenshots).mockReturnValueOnce(slow.promise);
    const pending = model.loadMedia();
    model.game.value = { ...game, id: otherId };
    slow.resolve([{ id: "stale" } as media.MediaItem]);
    await pending;
    expect(model.mediaItems.value).toEqual([]);
    expect(model.mediaLoadedFor.value).toBeNull();
  });

  it("keeps the newest account's media when an older request answers last", async () => {
    const model = await detail();
    const first = deferred<media.MediaItem[]>();
    const second = deferred<media.MediaItem[]>();
    vi.mocked(media.listGameScreenshots)
      .mockReturnValueOnce(first.promise)
      .mockReturnValueOnce(second.promise);
    const a = model.loadMedia("account-a");
    const b = model.loadMedia("account-b");
    second.resolve([{ id: "b" } as media.MediaItem]);
    await b;
    first.resolve([{ id: "a" } as media.MediaItem]);
    await a;
    expect(model.mediaItems.value.map((item) => item.id)).toEqual(["b"]);
    expect(model.mediaLoading.value).toBe(false);
  });

  it("ignores a files answer for another game and keeps the first failure retryable", async () => {
    const model = await detail();
    const slow = deferred<media.GameFile[]>();
    vi.mocked(media.listGameFiles).mockReturnValueOnce(slow.promise);
    const pending = model.loadGameFiles("doc");
    model.game.value = { ...game, id: otherId };
    slow.resolve([{ id: "stale" } as media.GameFile]);
    await pending;
    expect(model.docsFiles.value).toEqual([]);
    expect(model.filesLoaded.value.doc).toBeNull();

    model.game.value = game;
    vi.mocked(media.listGameFiles).mockRejectedValueOnce(new Error("Down"));
    await model.loadGameFiles("doc");
    expect(model.filesError.value).toBe("Down");
    expect(model.filesLoaded.value.doc).toBeNull();
    await model.loadGameFiles("doc"); // Retry
    expect(model.filesError.value).toBeNull();
    expect(model.filesLoaded.value.doc).toBe(mainId);
  });

  it("clears the saves error when a retry succeeds", async () => {
    const model = await detail();
    vi.mocked(archives.fetchArchives).mockRejectedValueOnce(new Error("Down"));
    await model.refreshSaveArchives();
    expect(model.filesError.value).toBe("Down");
    expect(model.saveArchivesLoaded.value).toBe(false);
    await model.refreshSaveArchives();
    expect(model.filesError.value).toBeNull();
    expect(model.saveArchivesLoaded.value).toBe(true);
  });

  it("says so when an edit was saved but re-reading the game failed", async () => {
    const model = await detail();
    vi.mocked(games.fetchGame).mockRejectedValueOnce(new Error("offline"));
    await model.onGameSaved();
    expect(model.refreshError.value).toBe(true);
    expect(model.game.value?.id).toBe(mainId); // what was on screen is kept
    await model.retryGame();
    expect(model.refreshError.value).toBe(false);
  });
});

describe("media and archive panels when the first load failed", () => {
  const mediaProps = {
    kind: "doc" as const,
    items: [],
    trash: [],
    uploading: false,
  };

  it("shows Retry instead of an Add prompt, and a skeleton while loading", async () => {
    const wrapper = mount(GameMediaPanel, {
      props: { ...mediaProps, loading: true, error: "Failed to load files" },
    });
    expect(wrapper.text()).toContain("Failed to load files");
    expect(wrapper.text()).not.toContain("Add");
    await wrapper
      .findAll("button")
      .find((b) => b.text() === "Retry")!
      .trigger("click");
    expect(wrapper.emitted("retry")).toHaveLength(1);

    await wrapper.setProps({ error: null });
    expect(wrapper.find('[aria-busy="true"]').exists()).toBe(true);
    expect(wrapper.text()).not.toContain("Retry");
  });

  it("an action error on a loaded, empty list still offers the Add prompt", () => {
    const wrapper = mount(GameMediaPanel, {
      props: { ...mediaProps, loading: false, error: "Failed to delete" },
    });
    expect(wrapper.text()).toContain("Add");
    expect(wrapper.text()).not.toContain("Retry");
  });

  it("archives: failed first load offers Retry and no Add prompt", async () => {
    const wrapper = mount(GameArchivesPanel, {
      props: {
        title: "Saves",
        plural: "saves",
        singular: "save",
        hint: "hint",
        archives: [],
        trash: [],
        loaded: false,
        uploading: false,
        error: "Failed to load saves",
      },
    });
    expect(wrapper.text()).not.toContain("Add a save");
    await wrapper
      .findAll("button")
      .find((b) => b.text() === "Retry")!
      .trigger("click");
    expect(wrapper.emitted("retry")).toHaveLength(1);
  });
});

describe("notes panel", () => {
  const summary = (name: string): games.GameNoteSummary => ({
    name,
    preview: "",
    tags: [],
    pinned: false,
    created_at: 1,
    updated_at: 1,
    linked_achievement_id: null,
    tasks_done: 0,
    tasks_total: 0,
    size: 10,
    words: 2,
  });
  const mountNotes = (gameId: string) =>
    mount(GameNotesPanel, {
      props: { gameId },
      global: { stubs: { teleport: true } },
    });

  it("shows Retry, not 'Write your first note', when the list cannot be loaded", async () => {
    vi.mocked(games.listGameNoteSummaries)
      .mockRejectedValueOnce(new Error("Notes unavailable"))
      .mockResolvedValueOnce([summary("Boss tips")]);
    const wrapper = mountNotes(mainId);
    await flushPromises();
    expect(wrapper.text()).toContain("Notes unavailable");
    expect(wrapper.text()).not.toContain("Write your first note");
    await wrapper
      .findAll("button")
      .find((b) => b.text() === "Retry")!
      .trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("Boss tips");
    expect(wrapper.text()).not.toContain("Notes unavailable");
  });

  it("does not show the previous game's notes, or a late answer, for the next game", async () => {
    const slow = deferred<games.GameNoteSummary[]>();
    vi.mocked(games.listGameNoteSummaries)
      .mockResolvedValueOnce([summary("Game A note")])
      .mockReturnValueOnce(slow.promise)
      .mockResolvedValueOnce([summary("Game B note")]);
    const wrapper = mountNotes(mainId);
    await flushPromises();
    expect(wrapper.text()).toContain("Game A note");

    await wrapper.setProps({ gameId: otherId }); // B starts loading (slow)
    expect(wrapper.text()).not.toContain("Game A note");
    await wrapper.setProps({ gameId: mainId }); // and back: a newer request
    await flushPromises();
    slow.resolve([summary("Stale")]);
    await flushPromises();
    expect(wrapper.text()).toContain("Game B note");
    expect(wrapper.text()).not.toContain("Stale");
  });
});

describe("game page quick changes", () => {
  it("restores a failed status on its own game and says so", async () => {
    const model = await detail();
    model.game.value = { ...game, status: "backlog" };
    const shown = model.game.value;
    const slow = deferred<void>();
    vi.mocked(games.setStatus).mockReturnValueOnce(
      slow.promise as unknown as ReturnType<typeof games.setStatus>,
    );
    const pending = model.changeStatus("playing");
    expect(shown.status).toBe("playing");
    const other = { ...game, id: otherId, status: "wishlist" as const };
    model.game.value = other;
    slow.reject(new Error("Down"));
    await pending;
    expect(shown.status).toBe("backlog");
    expect(model.game.value!.status).toBe("wishlist");
    // The error belongs to the game that failed, not the one now shown.
    expect(model.quickEditError.value).toBeNull();
  });

  it("saves quick status changes in order and keeps the newest one", async () => {
    const model = await detail();
    model.game.value = { ...game, status: "backlog" };
    const first = deferred<void>();
    const calls: string[] = [];
    vi.mocked(games.setStatus).mockImplementation(async (_id, status) => {
      calls.push(status);
      if (status === "playing") await first.promise;
      return undefined as unknown as Awaited<
        ReturnType<typeof games.setStatus>
      >;
    });
    const a = model.changeStatus("playing");
    const b = model.changeStatus("beaten");
    await flushPromises();
    expect(calls).toEqual(["playing"]);
    first.reject(new Error("Down"));
    await Promise.all([a, b]);
    expect(calls).toEqual(["playing", "beaten"]);
    expect(model.game.value!.status).toBe("beaten");
  });

  it("returns to the last accepted rating when the newest save fails", async () => {
    const model = await detail();
    model.game.value = { ...game, ratingOverall: 3 };
    vi.mocked(games.setRatings).mockRejectedValueOnce(new Error("Down"));
    await model.onRatingsChange({
      ratingOverall: 5,
      ratingStory: null,
      ratingGameplay: null,
      ratingSound: null,
    });
    expect(model.game.value!.ratingOverall).toBe(3);
    expect(model.quickEditError.value).toMatch(/rating/);
  });

  it("retries the similar-games library after a failure", async () => {
    vi.mocked(games.fetchGames).mockRejectedValueOnce(new Error("Down"));
    const model = await detail(); // mounting loads the game and the library once
    expect(games.fetchGames).toHaveBeenCalledTimes(1);
    vi.mocked(games.fetchGames).mockResolvedValueOnce([game]);
    model.retryGame();
    await flushPromises();
    expect(games.fetchGames).toHaveBeenCalledTimes(2);
    model.retryGame();
    await flushPromises();
    expect(games.fetchGames).toHaveBeenCalledTimes(2); // loaded once, kept
  });
});
