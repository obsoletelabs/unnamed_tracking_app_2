// @vitest-environment happy-dom
// Regression coverage for the loading/error/retry audit (wiki: development/state-audit).
import { flushPromises, shallowMount } from "@vue/test-utils";
import { reactive } from "vue";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const route = reactive<{ params: Record<string, string> }>({ params: {} });
vi.mock("vue-router", () => ({
  useRoute: () => route,
  useRouter: () => ({ push: vi.fn(), back: vi.fn() }),
  onBeforeRouteLeave: vi.fn(),
}));

const fetchMediaListDetail = vi.fn();
const fetchMediaLists = vi.fn();
vi.mock("../services/mediaExtras", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../services/mediaExtras")>()),
  fetchMediaListDetail: (...args: unknown[]) => fetchMediaListDetail(...args),
  fetchMediaLists: (...args: unknown[]) => fetchMediaLists(...args),
}));

const fetchCollectionIndex = vi.fn();
vi.mock("../services/collections", () => ({
  fetchCollectionIndex: (...args: unknown[]) => fetchCollectionIndex(...args),
}));
const listGameNoteSummaries = vi.fn();
vi.mock("../services/games", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../services/games")>()),
  fetchGame: vi.fn().mockResolvedValue(null),
  fetchGameAchievements: vi.fn().mockResolvedValue([]),
  listGameNoteSummaries: (...args: unknown[]) => listGameNoteSummaries(...args),
}));

const listUsers = vi.fn();
vi.mock("../services/admin", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../services/admin")>()),
  listUsers: (...args: unknown[]) => listUsers(...args),
}));
const listInvitations = vi.fn();
vi.mock("../services/adminUsers", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../services/adminUsers")>()),
  listInvitations: (...args: unknown[]) => listInvitations(...args),
}));
vi.mock("../services/passwordPolicy", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../services/passwordPolicy")>()),
  fetchPasswordPolicy: vi.fn().mockResolvedValue(null),
}));

import MediaListDetailView from "../views/MediaListDetail.vue";
import MediaListsView from "../views/MediaLists.vue";
import CollectionsView from "../views/Collections.vue";
import CollectionDetailView from "../views/CollectionDetail.vue";
import AchievementDetailView from "../views/AchievementDetail.vue";
import AdminSection from "../components/settings/AdminSection.vue";

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

const mounted: Array<{ unmount: () => void }> = [];
function mountView(component: Parameters<typeof shallowMount>[0]) {
  const wrapper = shallowMount(component);
  mounted.push(wrapper);
  return wrapper;
}
afterEach(() => {
  while (mounted.length) mounted.pop()?.unmount();
});

function retryButton(wrapper: ReturnType<typeof shallowMount>) {
  return wrapper.findAll("button").find((b) => b.text() === "Retry");
}

beforeEach(() => {
  vi.clearAllMocks();
  route.params = {};
});

describe("media list detail", () => {
  const detail = (id: string) => ({
    id,
    name: `List ${id}`,
    items: [],
    typeCounts: {},
    previewPosters: [],
    isSmart: false,
    isSystem: false,
  });

  it("ignores an out-of-order response for the previous list", async () => {
    const first = deferred<unknown>();
    const second = deferred<unknown>();
    fetchMediaListDetail
      .mockReturnValueOnce(first.promise)
      .mockReturnValueOnce(second.promise);
    route.params = { id: "a" };
    const wrapper = mountView(MediaListDetailView);
    route.params = { id: "b" };
    await flushPromises();
    second.resolve(detail("b"));
    await flushPromises();
    first.resolve(detail("a"));
    await flushPromises();
    expect(wrapper.html()).toContain("List b");
    expect(wrapper.html()).not.toContain("List a");
  });

  it("offers a retry after a failed load that recovers", async () => {
    fetchMediaListDetail
      .mockRejectedValueOnce(new Error("boom"))
      .mockResolvedValueOnce(detail("a"));
    route.params = { id: "a" };
    const wrapper = mountView(MediaListDetailView);
    await flushPromises();
    expect(wrapper.text()).toContain("boom");
    await retryButton(wrapper)?.trigger("click");
    await flushPromises();
    expect(wrapper.html()).toContain("List a");
    expect(retryButton(wrapper)).toBeUndefined();
  });
});

describe("list and collection indexes", () => {
  it("retries a failed lists load without double-submitting", async () => {
    const pending = deferred<unknown[]>();
    fetchMediaLists
      .mockRejectedValueOnce(new Error("lists down"))
      .mockReturnValueOnce(pending.promise);
    const wrapper = mountView(MediaListsView);
    await flushPromises();
    expect(wrapper.text()).toContain("lists down");
    await retryButton(wrapper)?.trigger("click");
    await retryButton(wrapper)?.trigger("click");
    expect(fetchMediaLists).toHaveBeenCalledTimes(2);
    pending.resolve([]);
    await flushPromises();
    expect(wrapper.text()).not.toContain("lists down");
  });

  it("retries a failed collections load", async () => {
    fetchCollectionIndex
      .mockRejectedValueOnce(new Error("games down"))
      .mockResolvedValueOnce([]);
    const wrapper = mountView(CollectionsView);
    await flushPromises();
    expect(wrapper.text()).toContain("games down");
    await retryButton(wrapper)?.trigger("click");
    await flushPromises();
    expect(fetchCollectionIndex).toHaveBeenCalledTimes(2);
    expect(wrapper.text()).not.toContain("games down");
  });
});

describe("collection detail", () => {
  it("says the load failed instead of showing an empty collection", async () => {
    route.params = { name: "Favourites" };
    fetchCollectionIndex
      .mockRejectedValueOnce(new Error("offline"))
      .mockResolvedValueOnce([]);
    const wrapper = mountView(CollectionDetailView);
    await flushPromises();
    expect(wrapper.text()).toContain("Could not load this collection");
    expect(wrapper.text()).not.toContain("Nothing in this collection yet");
    await retryButton(wrapper)?.trigger("click");
    await flushPromises();
    expect(wrapper.text()).not.toContain("Could not load this collection");
    expect(wrapper.text()).toContain("Nothing in this collection yet");
  });

  it("does not start a second load while one is running", async () => {
    route.params = { name: "Favourites" };
    const pending = deferred<unknown[]>();
    fetchCollectionIndex.mockReturnValue(pending.promise);
    mountView(CollectionDetailView);
    await flushPromises();
    expect(fetchCollectionIndex).toHaveBeenCalledTimes(1);
  });
});

describe("achievement detail tied notes", () => {
  it("drops notes from a response that finished after navigating away", async () => {
    const first = deferred<unknown[]>();
    const second = deferred<unknown[]>();
    listGameNoteSummaries
      .mockReturnValueOnce(first.promise)
      .mockReturnValueOnce(second.promise);
    route.params = { gameId: "g", achievementId: "one" };
    const wrapper = mountView(AchievementDetailView);
    route.params = { gameId: "g", achievementId: "two" };
    await flushPromises();
    second.resolve([]);
    await flushPromises();
    first.resolve([{ name: "stale", linked_achievement_id: "one" }]);
    await flushPromises();
    expect(listGameNoteSummaries).toHaveBeenCalledTimes(2);
    expect(wrapper.html()).not.toContain("stale");
  });
});

describe("admin users", () => {
  const user = {
    id: "u1",
    username: "alice",
    email: "alice@example.test",
    is_admin: false,
    created_at: 1,
  };

  it("shows an invitation failure instead of an empty list and retries it", async () => {
    listUsers.mockResolvedValue([user]);
    listInvitations
      .mockRejectedValueOnce(new Error("invites down"))
      .mockResolvedValueOnce([]);
    const wrapper = mountView(AdminSection);
    await flushPromises();
    expect(wrapper.text()).toContain("alice");
    expect(wrapper.text()).toContain("Invitations could not be loaded");
    await retryButton(wrapper)?.trigger("click");
    await flushPromises();
    expect(wrapper.text()).not.toContain("Invitations could not be loaded");
  });

  it("offers a retry when the user list cannot load", async () => {
    listUsers
      .mockRejectedValueOnce(new Error("users down"))
      .mockResolvedValueOnce([user]);
    listInvitations.mockResolvedValue([]);
    const wrapper = mountView(AdminSection);
    await flushPromises();
    expect(wrapper.text()).toContain("users down");
    await retryButton(wrapper)?.trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("alice");
    expect(wrapper.text()).not.toContain("users down");
  });
});
