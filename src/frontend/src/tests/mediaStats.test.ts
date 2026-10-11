import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { effectScope, ref } from "vue";
import { useMediaStats } from "../composables/useMediaStats";
import {
  fetchMediaStats,
  peekMediaStats,
  readMediaStatsSection,
} from "../services/mediaStats";
import type { MediaStatsSection } from "../services/mediaStats";

const hooks = vi.hoisted(() => ({
  mounted: [] as (() => void)[],
  activated: [] as (() => void)[],
  deactivated: [] as (() => void)[],
}));
vi.mock("vue", async (original) => ({
  ...(await original<typeof import("vue")>()),
  onMounted: (fn: () => void) => hooks.mounted.push(fn),
  onActivated: (fn: () => void) => hooks.activated.push(fn),
  onDeactivated: (fn: () => void) => hooks.deactivated.push(fn),
}));
let scope = effectScope();
const requests: {
  section: MediaStatsSection;
  signal: AbortSignal;
  finish: (response: Response) => void;
}[] = [];
const fetch = vi.fn<(url: string, options: RequestInit) => Promise<Response>>();

function response(section: MediaStatsSection, titles = 1) {
  return new Response(
    JSON.stringify({
      [section]: {
        titles,
        top_rated: [{ poster_url: "/cover", title: "Private title" }],
        ...(section === "overview"
          ? { in_progress: [], needs_score: { items: [] } }
          : {}),
      },
    }),
  );
}
async function flush() {
  // Response.json resolves on a later event-loop turn in the Node environment.
  await new Promise((resolve) => setTimeout(resolve, 0));
}
function mount() {
  const section = ref<MediaStatsSection>("games");
  const actor = ref<string | null>("actor-a");
  const state = scope.run(() => useMediaStats(section, actor))!;
  hooks.mounted.forEach((fn) => fn());
  hooks.activated.forEach((fn) => fn());
  return { section, actor, state };
}
beforeEach(() => {
  scope = effectScope();
  requests.length = 0;
  peekMediaStats(null);
  fetch.mockImplementation(
    (url, options) =>
      new Promise<Response>((finish) => {
        requests.push({
          section: new URL(url, "http://test").searchParams.get(
            "section",
          ) as MediaStatsSection,
          signal: options.signal as AbortSignal,
          finish,
        });
      }),
  );
  vi.stubGlobal("fetch", fetch);
});
afterEach(() => {
  scope.stop();
  Object.values(hooks).forEach((list) => {
    list.length = 0;
  });
  fetch.mockClear();
  vi.unstubAllGlobals();
});

describe("statistics tab requests", () => {
  it("loads one tab on mount and reuses previously visited tabs without another request", async () => {
    const { section, state } = mount();
    expect(requests.map((r) => r.section)).toEqual(["games"]);
    requests[0]!.finish(response("games", 3));
    await flush();
    expect(state.stats.value.games?.titles).toBe(3);
    expect(state.stats.value.games?.top_rated[0]?.posterUrl).toBe("/cover");
    section.value = "movie";
    expect(state.hasData.value).toBe(false);
    requests[1]!.finish(response("movie", 4));
    await flush();
    section.value = "games";
    expect(requests).toHaveLength(2);
    expect(state.hasData.value).toBe(true);
    expect(state.stats.value.games?.titles).toBe(3);
    hooks.deactivated.forEach((fn) => fn());
    hooks.activated.forEach((fn) => fn());
    requests[2]!.finish(response("games", 5));
    await flush();
    section.value = "movie";
    expect(state.stats.value.movie?.titles).toBe(4);
    expect(state.loading.value).toBe(true);
    requests[3]!.finish(response("movie", 6));
    await flush();
    expect(state.stats.value.movie?.titles).toBe(6);
  });
  it("aborts a previous tab and ignores its late result without replacing the new tab", async () => {
    const { section, state } = mount();
    section.value = "tv";
    expect(requests[0]!.signal.aborted).toBe(true);
    requests[1]!.finish(response("tv", 2));
    requests[0]!.finish(response("games", 999));
    await flush();
    expect(state.stats.value.tv?.titles).toBe(2);
    expect(state.stats.value.games).toBeUndefined();
    expect(state.error.value).toBeNull();
    expect(state.loading.value).toBe(false);
  });
  it("shows cached numbers while revalidating after page reactivation and reports a failed refresh", async () => {
    const { state } = mount();
    requests[0]!.finish(response("games", 3));
    await flush();
    hooks.deactivated.forEach((fn) => fn());
    hooks.activated.forEach((fn) => fn());
    expect(state.stats.value.games?.titles).toBe(3);
    expect(state.loading.value).toBe(true);
    requests[1]!.finish(new Response(null, { status: 503 }));
    await flush();
    expect(state.stats.value.games?.titles).toBe(3);
    expect(state.error.value).toContain("503");
    const retry = state.load();
    requests[2]!.finish(response("games", 5));
    await retry;
    expect(state.stats.value.games?.titles).toBe(5);
    expect(state.error.value).toBeNull();
  });
  it("clears another actor's cached numbers and cancels late private responses", async () => {
    const { actor, state } = mount();
    requests[0]!.finish(response("games", 3));
    await flush();
    const pending = state.load();
    actor.value = "actor-b";
    expect(state.stats.value).toEqual({});
    requests[1]!.finish(response("games", 999));
    requests[2]!.finish(response("games", 7));
    await pending;
    await flush();
    expect(state.stats.value.games?.titles).toBe(7);
    actor.value = null;
    expect(state.stats.value).toEqual({});
    expect(state.loading.value).toBe(false);
    expect(requests).toHaveLength(3);
  });
  it("cancels inactive work and defers tab changes until the page is active", async () => {
    const { section, state } = mount();
    hooks.deactivated.forEach((fn) => fn());
    expect(requests[0]!.signal.aborted).toBe(true);
    section.value = "anime";
    expect(requests).toHaveLength(1);
    requests[0]!.finish(response("games"));
    await flush();
    expect(state.stats.value).toEqual({});
    hooks.activated.forEach((fn) => fn());
    expect(requests[1]!.section).toBe("anime");
    requests[1]!.finish(response("anime"));
    await flush();
    expect(state.hasData.value).toBe(true);
  });
  it("rejects a missing requested section instead of caching an unrelated response", async () => {
    const pending = fetchMediaStats(
      "games",
      "actor-a",
      new AbortController().signal,
    );
    requests[0]!.finish(response("movie"));
    await expect(pending).rejects.toThrow("section is missing");
    expect(peekMediaStats("actor-a")).toEqual({});
  });
  it("falls back to Overview for invalid or absent saved tab values", () => {
    expect(readMediaStatsSection(null)).toBe("overview");
    expect(readMediaStatsSection("unknown")).toBe("overview");
    expect(readMediaStatsSection("games")).toBe("games");
    expect(readMediaStatsSection("anime")).toBe("anime");
  });
});
