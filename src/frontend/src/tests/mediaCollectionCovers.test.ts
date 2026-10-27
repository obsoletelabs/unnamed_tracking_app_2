// @vitest-environment happy-dom
import { afterEach, expect, it, vi } from "vitest";
import { shallowMount } from "@vue/test-utils";
import CollectionTile from "../components/CollectionTile.vue";
import { currentUser } from "../state/auth";
import type { CurrentUser } from "../services/auth";
import {
  mediaCollectionCoverImage,
  uploadMediaCollectionCover,
} from "../services/mediaCollectionCovers";

afterEach(() => {
  currentUser.value = null;
  vi.unstubAllGlobals();
});

it("renders one composite instead of four poster requests", () => {
  const wrapper = shallowMount(CollectionTile, {
    props: {
      id: "collection",
      name: "Collection",
      covers: ["1.jpg", "2.jpg", "3.jpg", "4.jpg"],
      coverImage: "/api/lists/collection/cover?owner=member",
      count: 4,
      countNoun: "title",
      noun: "list",
    },
  });
  expect(wrapper.findAllComponents({ name: "GameArtwork" })).toHaveLength(1);
  expect(wrapper.findComponent({ name: "GameArtwork" }).props("src")).toBe(
    "/api/lists/collection/cover?owner=member",
  );
  wrapper.unmount();
});

it("partitions upload revisions by account and does not need a follow-up metadata read", async () => {
  currentUser.value = { id: "first" } as CurrentUser;
  const fetcher = vi.fn(
    async () =>
      new Response(JSON.stringify({ revision: "uploaded" }), { status: 200 }),
  );
  vi.stubGlobal("fetch", fetcher);
  const list = {
    id: "same-list",
    coverUrl: "/api/lists/same-list/cover?owner=first&v=initial",
  };
  await uploadMediaCollectionCover(list.id, new File(["image"], "cover.png"));
  expect(fetcher).toHaveBeenCalledOnce();
  expect(mediaCollectionCoverImage(list)).toContain("r=uploaded");
  currentUser.value = { id: "second" } as CurrentUser;
  expect(
    mediaCollectionCoverImage({
      ...list,
      coverUrl: "/api/lists/same-list/cover?owner=second",
    }),
  ).not.toContain("uploaded");
});
