// @vitest-environment happy-dom
import { flushPromises, mount, type VueWrapper } from "@vue/test-utils";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { useGameDetail } from "../composables/useGameDetail";
import { mockGames } from "../data/mockGames";
import * as games from "../services/games";
import * as media from "../services/media";
import * as archives from "../services/gameArchives";
import type { GameCopyLabel } from "../types/game";

vi.mock("../services/games");
vi.mock("../services/media");
vi.mock("../services/gameArchives");
vi.mock("vue-router", () => ({
  useRoute: () => ({ params: { id: "main" }, query: {} }),
  useRouter: () => ({ push: vi.fn() }),
}));
const wrappers: VueWrapper[] = [];
const copy: GameCopyLabel = {
  game_id: "other",
  title: "Minecraft",
  source: "Epic",
  platform: "PC",
  is_main: false,
};
const game = { ...mockGames[0]!, id: "main", title: "Minecraft" };
function file(id: string, owner: string): media.GameFile {
  return {
    id,
    game_id: owner,
    filename: "guide.txt",
    kind: "doc",
    size: 1,
    url: `/${owner}/guide.txt`,
    tags: [],
    note: null,
    created_at: 1,
    copy,
  };
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

it("removes only the owning copy's document when copies share a filename", async () => {
  const model = await detail();
  const own = file("own-file", "main");
  const other = file("other-file", "other");
  model.docsFiles.value = [own, other];
  await model.removeGameFile("doc", other);
  expect(media.deleteGameFile).toHaveBeenCalledWith(
    "other",
    "doc",
    "guide.txt",
  );
  expect(model.docsFiles.value.map((item) => item.id)).toEqual(["own-file"]);
});

it("keeps a document's copy label after saving its details", async () => {
  const model = await detail();
  const other = file("other-file", "other");
  model.docsFiles.value = [other];
  const updated = { ...other };
  delete updated.copy;
  vi.mocked(media.updateGameFile).mockResolvedValue({
    ...updated,
    note: "Edited",
  });
  await model.saveGameFile("doc", other, { note: "Edited" });
  expect(media.updateGameFile).toHaveBeenCalledWith(
    "other",
    "doc",
    "other-file",
    {
      note: "Edited",
    },
  );
  expect(model.docsFiles.value[0]?.copy).toEqual(copy);
  expect(model.docsFiles.value[0]?.note).toBe("Edited");
});

it("keeps a media item's copy label after editing or detecting its date", async () => {
  const model = await detail();
  const item: media.MediaItem = {
    ...file("image", "other"),
    kind: "screenshot",
    linked_achievement_id: null,
    profile_id: null,
  };
  model.mediaItems.value = [item];
  const updated = { ...item, note: "Edited" };
  delete updated.copy;
  vi.mocked(media.updateMediaItem).mockResolvedValue(updated);
  await model.saveMediaItem(item, { note: "Edited" });
  expect(model.mediaItems.value[0]?.copy).toEqual(copy);
  vi.mocked(media.detectMediaDates).mockResolvedValue([
    { ...updated, taken_at: 123 },
  ]);
  await model.detectOne(item);
  expect(media.detectMediaDates).toHaveBeenCalledWith("other", ["image"]);
  expect(model.mediaItems.value[0]?.copy).toEqual(copy);
  expect(model.mediaItems.value[0]?.taken_at).toBe(123);
});

it("reloads cached copy content after a merge or split on the same game page", async () => {
  const model = await detail();
  model.activeTab.value = "Docs";
  await flushPromises();
  const other = file("other-file", "other");
  model.docsFiles.value = [other];
  model.saveArchivesLoaded.value = true;
  vi.mocked(media.listGameFiles).mockClear();
  await model.onCopiesChanged();
  await flushPromises();
  expect(media.listGameFiles).toHaveBeenCalledWith("main", "doc", true);
  expect(model.docsFiles.value).toEqual([]);
  expect(model.saveArchivesLoaded.value).toBe(false);
  expect(games.fetchContentCounts).toHaveBeenCalledWith("main", true);
});

it("lists world saves across copies and adds versions to the owning copy", async () => {
  const model = await detail();
  const world: archives.GameArchiveData = {
    id: "world",
    name: "Other world",
    kind: "world_save",
    game_id: "other",
    copy,
    created_at: 1,
    updated_at: 1,
    versions: [],
  };
  vi.mocked(archives.fetchArchives).mockResolvedValue([world]);
  model.activeTab.value = "Worlds";
  await flushPromises();
  expect(archives.fetchArchives).toHaveBeenCalledWith(
    "main",
    "world_save",
    true,
  );
  expect(model.worlds.value).toEqual([world]);
  const upload = new File(["world"], "world.zip");
  await model.onAddWorldVersion(world, [upload]);
  expect(archives.addArchiveVersion).toHaveBeenCalledWith(
    "other",
    "world",
    upload,
    expect.any(Function),
  );
});
