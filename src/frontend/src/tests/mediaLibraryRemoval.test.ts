// @vitest-environment happy-dom
import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import MediaLibraryView from "../components/library/MediaLibraryView.vue";
import { preferences } from "../state/preferences";
import type { LibraryCardVM } from "../types/mediaLibrary";

const confirm = vi.hoisted(() => vi.fn());
vi.mock("../state/dialog", () => ({ useConfirm: () => confirm }));
vi.mock("vue-router", () => ({
  useRoute: () => ({ path: "/movies", query: {} }),
  useRouter: () => ({ push: vi.fn() }),
}));
const wrappers: ReturnType<typeof mount>[] = [];
const card: LibraryCardVM = {
  id: "1",
  title: "First",
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
function library(
  remove: (
    ids: string[],
  ) => Promise<{ failedIds: string[]; error: string | null }>,
) {
  const wrapper = mount(MediaLibraryView, {
    props: {
      kind: "movie",
      addLabel: "Add movie",
      items: [card, { ...card, id: "2", title: "Second" }],
      total: 500,
      statusCounts: { WATCHED: 500 },
      loading: false,
      error: null,
      detailRoute: (id) => "/movies/" + id,
      onBulkDelete: remove,
    },
    global: { stubs: { MediaTopBar: true, RouterLink: true } },
  });
  wrappers.push(wrapper);
  return wrapper;
}
beforeEach(() => {
  vi.resetAllMocks();
  confirm.mockResolvedValue(true);
  localStorage.setItem("libraryLayout", "list");
  preferences.value.media_trash_retention_days = 7;
});
afterEach(() => {
  wrappers.splice(0).forEach((wrapper) => wrapper.unmount());
  localStorage.clear();
});
async function select(wrapper: ReturnType<typeof library>) {
  await wrapper.get(".select-btn").trigger("click");
  await wrapper
    .findAll("button")
    .find((button) => button.text() === "Select shown titles")!
    .trigger("click");
}
async function remove(wrapper: ReturnType<typeof library>) {
  await wrapper
    .findAll("button")
    .find((button) => button.text() === "Remove from library")!
    .trigger("click");
  await flushPromises();
}

it("selects shown titles without including the unloaded library, and keeps selection on cancel", async () => {
  const action = vi.fn().mockResolvedValue({ failedIds: [], error: null });
  const wrapper = library(action);
  await select(wrapper);
  expect(wrapper.get(".bulk-bar .count").text()).toBe("2 selected");
  confirm.mockResolvedValueOnce(false);
  await remove(wrapper);
  expect(action).not.toHaveBeenCalled();
  expect(wrapper.get(".bulk-bar .count").text()).toBe("2 selected");
  expect(confirm).toHaveBeenCalledWith(
    expect.objectContaining({ message: expect.stringContaining("7 days") }),
  );
});

it("keeps only failed titles selected and retries them", async () => {
  const action = vi
    .fn()
    .mockResolvedValueOnce({ failedIds: ["2"], error: "Offline" })
    .mockResolvedValueOnce({ failedIds: [], error: null });
  const wrapper = library(action);
  await select(wrapper);
  await remove(wrapper);
  expect(action).toHaveBeenNthCalledWith(1, ["1", "2"]);
  expect(wrapper.get(".bulk-bar .count").text()).toBe("1 selected");
  expect(wrapper.get('[role="alert"]').text()).toContain(
    "1 of 2 titles could not be removed",
  );
  await remove(wrapper);
  expect(action).toHaveBeenNthCalledWith(2, ["2"]);
  expect(wrapper.find(".bulk-bar").exists()).toBe(false);
});

it("explains retained Trash and blocks a second submission", async () => {
  preferences.value.media_trash_retention_days = 0;
  let finish!: (value: { failedIds: string[]; error: string | null }) => void;
  const action = vi.fn().mockImplementation(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  const wrapper = library(action);
  await select(wrapper);
  const button = wrapper
    .findAll("button")
    .find((button) => button.text() === "Remove from library")!;
  await button.trigger("click");
  await flushPromises();
  await button.trigger("click");
  expect(action).toHaveBeenCalledTimes(1);
  expect(button.attributes("disabled")).toBeDefined();
  expect(confirm).toHaveBeenCalledWith(
    expect.objectContaining({
      message: expect.stringContaining("Automatic deletion is off"),
    }),
  );
  finish({ failedIds: [], error: null });
  await flushPromises();
});
