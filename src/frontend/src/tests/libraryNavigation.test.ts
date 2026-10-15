// @vitest-environment happy-dom
import { afterEach, expect, it, vi } from "vitest";
import { flushPromises, shallowMount } from "@vue/test-utils";
import CollectionDetail from "../views/CollectionDetail.vue";
import MediaListDetailView from "../views/MediaListDetail.vue";
import CollectionDetailHeader from "../components/CollectionDetailHeader.vue";
import CollectionItemTile from "../components/CollectionItemTile.vue";
import {
  fetchMediaListDetail,
  reorderMediaList,
  type MediaListDetail,
} from "../services/mediaExtras";

const routing = vi.hoisted(() => ({ params: { name: "50% Off", id: "list" } }));
vi.mock("vue-router", async (original) => ({
  ...(await original<typeof import("vue-router")>()),
  useRoute: () => routing,
  useRouter: () => ({ push: vi.fn(), back: vi.fn() }),
}));
vi.mock("../services/games", async (original) => ({
  ...(await original<typeof import("../services/games")>()),
  fetchGames: vi.fn(async () => []),
}));
vi.mock("../services/mediaExtras", async (original) => ({
  ...(await original<typeof import("../services/mediaExtras")>()),
  fetchMediaListDetail: vi.fn(),
  reorderMediaList: vi.fn(async () => {}),
}));

afterEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
});

it.each(["50% Off", "日本語", "Parent/50% Off", "100%2F literal"])(
  "uses the already-decoded collection name %s",
  async (name) => {
    routing.params.name = name;
    const view = shallowMount(CollectionDetail);
    await flushPromises();
    const header = view.getComponent(CollectionDetailHeader);
    expect(header.props("title")).toBe(
      name.includes("/") ? name.split("/").slice(1).join("/") : name,
    );
    expect(header.props("crumb")?.label ?? null).toBe(
      name.includes("/") ? "Parent" : null,
    );
    view.unmount();
  },
);

it("clears a type filter before reordering and nudges the visible full-list item", async () => {
  const list: MediaListDetail = {
    id: "list",
    name: "Weekend",
    description: null,
    itemCount: 3,
    isSmart: false,
    isSystem: false,
    pinned: false,
    position: 0,
    typeCounts: { movie: 2, tv: 1 },
    smartRule: null,
    coverMediaId: null,
    previewPosters: [],
    createdAt: 0,
    updatedAt: 0,
    items: ["movie", "tv", "movie"].map((type, index) => ({
      id: String(index),
      mediaType: type as "movie" | "tv",
      mediaId: String(index),
      title: `Title ${index}`,
      posterUrl: null,
      status: "backlog",
      addedAt: 0,
    })),
  };
  vi.mocked(fetchMediaListDetail).mockResolvedValue(list);
  const view = shallowMount(MediaListDetailView);
  await flushPromises();
  const filter = view
    .findAll("button")
    .find((button) => button.text().startsWith("Movies"))!;
  await filter.trigger("click");
  expect(view.findAllComponents(CollectionItemTile)).toHaveLength(2);
  view.getComponent(CollectionDetailHeader).vm.$emit("reorder");
  await flushPromises();
  expect(view.findAllComponents(CollectionItemTile)).toHaveLength(3);
  expect(filter.attributes("disabled")).toBeDefined();
  view.findAllComponents(CollectionItemTile)[1]!.vm.$emit("nudge", -1);
  await flushPromises();
  expect(reorderMediaList).toHaveBeenCalledWith("list", ["1", "0", "2"]);
  view.unmount();
});
