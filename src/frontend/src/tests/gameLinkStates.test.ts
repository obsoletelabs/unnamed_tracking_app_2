// @vitest-environment happy-dom
import { flushPromises, mount, type VueWrapper } from "@vue/test-utils";
import { reactive } from "vue";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { useGameDetail } from "../composables/useGameDetail";
import { ApiRequestError } from "../services/apiError";
import * as games from "../services/games";
import GameUnavailable from "../components/game/GameUnavailable.vue";
import { mockGames } from "../data/mockGames";
import { GAME_REQUEST_TIMEOUT_MS, isGameId } from "../utils/gameLink";

const ids = [
  "00000000-0000-4000-8000-000000000001",
  "00000000-0000-4000-8000-000000000002",
];
const route = vi.hoisted(() => ({ params: { id: "" }, query: {} }));
vi.mock("../services/games");
vi.mock("vue-router", () => ({
  useRoute: () => currentRoute,
  useRouter: () => ({ push: vi.fn(), back: vi.fn() }),
  RouterLink: { props: ["to"], template: '<a :href="to"><slot /></a>' },
}));
const currentRoute = reactive(route);
const wrappers: VueWrapper[] = [];
const loadedGame = { ...mockGames[0]!, id: ids[0]! };

function detail(id = ids[0]!) {
  currentRoute.params.id = id;
  let model!: ReturnType<typeof useGameDetail>;
  wrappers.push(
    mount({
      setup() {
        model = useGameDetail();
        return () => null;
      },
    }),
  );
  return model;
}
beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(games.fetchGame).mockResolvedValue(loadedGame);
  vi.mocked(games.fetchGames).mockResolvedValue([]);
  vi.mocked(games.fetchGameAchievements).mockResolvedValue([]);
  vi.mocked(games.fetchGameVariants).mockResolvedValue([]);
  vi.mocked(games.fetchContentCounts).mockResolvedValue({} as never);
});
afterEach(() => {
  wrappers.splice(0).forEach((wrapper) => wrapper.unmount());
  vi.useRealTimers();
});

it.each([
  "",
  "arbitrary",
  "639a9240-3c1c-4343-b75b-",
  "x".repeat(10000),
  "../games",
  "{00000000-0000-4000-8000-000000000001}",
])(
  "rejects malformed links without requesting game data, case %#",
  async (id) => {
    const model = detail(id);
    await flushPromises();
    expect(model.error.value).toBe("invalid");
    expect(model.loading.value).toBe(false);
    expect(model.game.value).toBeNull();
    expect(games.fetchGame).not.toHaveBeenCalled();
    expect(games.fetchGames).not.toHaveBeenCalled();
    expect(games.fetchGameAchievements).not.toHaveBeenCalled();
    expect(games.fetchGameVariants).not.toHaveBeenCalled();
  },
);

it("loads an ordinary valid link on its initial mount", async () => {
  const model = detail();
  expect(model.loading.value).toBe(true);
  await flushPromises();
  expect(games.fetchGame).toHaveBeenCalledWith(ids[0], expect.any(AbortSignal));
  expect(model.game.value?.id).toBe(ids[0]);
  expect(model.loading.value).toBe(false);
  expect(model.error.value).toBeNull();
  expect(isGameId(ids[0]!.toUpperCase())).toBe(true);
});

it("shows the same unavailable record state for every null/404 result", async () => {
  vi.mocked(games.fetchGame).mockResolvedValue(null);
  const model = detail();
  await flushPromises();
  expect(model.game.value).toBeNull();
  expect(model.loading.value).toBe(false);
  expect(model.error.value).toBeNull();
});

it.each([
  new ApiRequestError(422, "raw UUID 422"),
  new ApiRequestError(401, "private detail"),
  new ApiRequestError(500, "raw server trace"),
  new TypeError("raw network details"),
])(
  "maps failures to safe states and retries transient errors",
  async (error) => {
    vi.mocked(games.fetchGame).mockRejectedValueOnce(error);
    const model = detail();
    await flushPromises();
    expect(model.error.value).toBe(
      error instanceof ApiRequestError && error.status === 422
        ? "invalid"
        : error instanceof ApiRequestError && error.status === 401
          ? "signed-out"
          : "unavailable",
    );
    expect(model.game.value).toBeNull();
    await model.retryGame();
    expect(model.game.value?.id).toBe(ids[0]);
    expect(model.error.value).toBeNull();
  },
);

it("ignores stale failures and loading completion when a new route is pending", async () => {
  let rejectOld!: (error: Error) => void;
  let resolveNew!: (game: typeof loadedGame) => void;
  vi.mocked(games.fetchGame).mockImplementationOnce(
    () =>
      new Promise((_resolve, reject) => {
        rejectOld = reject;
      }),
  );
  vi.mocked(games.fetchGame).mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        resolveNew = resolve;
      }),
  );
  const model = detail();
  currentRoute.params.id = ids[1]!;
  await flushPromises();
  rejectOld(new Error("late old failure"));
  await flushPromises();
  expect(model.loading.value).toBe(true);
  expect(model.error.value).toBeNull();
  resolveNew({ ...loadedGame, id: ids[1]! });
  await flushPromises();
  expect(model.game.value?.id).toBe(ids[1]);
  expect(model.loading.value).toBe(false);
});

it("clears a previously loaded game immediately on navigation to an invalid link", async () => {
  const model = detail();
  await flushPromises();
  currentRoute.params.id = "broken";
  await flushPromises();
  expect(model.game.value).toBeNull();
  expect(model.error.value).toBe("invalid");
  expect(games.fetchGame).toHaveBeenCalledTimes(1);
});

it("ends a stalled detail request with a retry state", async () => {
  vi.useFakeTimers();
  vi.mocked(games.fetchGame).mockImplementation(
    (_id, signal) =>
      new Promise((_resolve, reject) => {
        signal!.addEventListener("abort", () =>
          reject(new DOMException("Aborted", "AbortError")),
        );
      }),
  );
  const model = detail();
  await vi.advanceTimersByTimeAsync(GAME_REQUEST_TIMEOUT_MS);
  expect(model.loading.value).toBe(false);
  expect(model.error.value).toBe("unavailable");
});

it("focuses the accessible heading, exposes working library navigation and emits retry", async () => {
  const wrapper = mount(GameUnavailable, {
    props: { kind: "unavailable" },
    attachTo: document.body,
  });
  wrappers.push(wrapper);
  await flushPromises();
  expect(document.activeElement).toBe(wrapper.get("h1").element);
  expect(wrapper.get('a[href="/games"]').text()).toBe("Back to games");
  await wrapper.get("button").trigger("click");
  expect(wrapper.emitted("retry")).toHaveLength(1);
  await wrapper.setProps({ kind: "invalid" });
  expect(wrapper.text()).toContain("Invalid game link");
  expect(wrapper.find("button").exists()).toBe(false);
  expect(wrapper.text()).not.toMatch(/422|UUID|raw|00000000/);
});
