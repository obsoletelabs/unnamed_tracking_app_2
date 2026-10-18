import { afterEach, expect, it, vi } from "vitest";
import { ref } from "vue";
import { useGameWorlds } from "../composables/useGameWorlds";
import type { Game } from "../types/game";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

function setup() {
  const game = ref<Game | null>({ id: "first" } as Game);
  const error = ref<string | null>(null);
  return {
    game,
    error,
    ...useGameWorlds(game, ref(new Set<string>()), error, async () => null),
  };
}

it("loads world archives once without renderer requests or background polling", async () => {
  vi.useFakeTimers();
  const fetch = vi.fn(async () => new Response("[]"));
  vi.stubGlobal("fetch", fetch);
  const worlds = setup();
  await worlds.refreshWorlds();
  expect(worlds.worldsLoaded.value).toBe(true);
  expect(fetch).toHaveBeenCalledWith(
    "/api/game/first/archives/world_save?group=true",
    {
      credentials: "include",
    },
  );
  await vi.advanceTimersByTimeAsync(60_000);
  expect(fetch).toHaveBeenCalledTimes(1);
});

it("does not replace a new game's worlds with a delayed old response", async () => {
  let resolve!: (value: Response) => void;
  vi.stubGlobal(
    "fetch",
    () => new Promise<Response>((done) => (resolve = done)),
  );
  const worlds = setup();
  const pending = worlds.refreshWorlds();
  worlds.game.value = { id: "second" } as Game;
  resolve(new Response('[{"id":"old-world"}]'));
  await pending;
  expect(worlds.worlds.value).toEqual([]);
  expect(worlds.worldsLoaded.value).toBe(false);
});

it("reports a failed archive load without clearing saved worlds", async () => {
  vi.stubGlobal("fetch", async () => new Response("[]"));
  const worlds = setup();
  await worlds.refreshWorlds();
  vi.stubGlobal("fetch", async () => {
    throw new Error("Archive request failed");
  });
  await worlds.refreshWorlds();
  expect(worlds.error.value).toBe("Archive request failed");
  expect(worlds.worldsLoaded.value).toBe(true);
  expect(worlds.worlds.value).toEqual([]);
});

it("ignores an old game's failed request after navigation", async () => {
  let reject!: (reason: Error) => void;
  vi.stubGlobal(
    "fetch",
    () => new Promise<Response>((_resolve, fail) => (reject = fail)),
  );
  const worlds = setup();
  const pending = worlds.refreshWorlds();
  worlds.game.value = { id: "second" } as Game;
  reject(new Error("Old request failed"));
  await pending;
  expect(worlds.error.value).toBeNull();
});
