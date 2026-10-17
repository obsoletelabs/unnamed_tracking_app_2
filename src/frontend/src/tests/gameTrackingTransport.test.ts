// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { createGame, updateGame, type NewGameInput } from "../services/games";
import { manualCollectionNames } from "../state/collectionMeta";
import { smartCollections } from "../state/smartCollections";
import { mockGames } from "../data/mockGames";

const input = {
  title: "Tracked",
  folderLocation: "Tracked",
  status: "playing",
  ownership: {},
  favorite: true,
  collections: ["Couch"],
  resumeNote: "Chapter 3",
  playtimeSeconds: 5400,
} as unknown as NewGameInput;

let fetchMock: ReturnType<typeof vi.fn>;
beforeEach(() => {
  vi.stubEnv("VITE_USE_MOCK_DATA", "false");
  // The request body is the contract here; a refusal skips response mapping.
  fetchMock = vi
    .fn()
    .mockResolvedValue(
      new Response(JSON.stringify({ detail: "Refused" }), { status: 409 }),
    );
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
  smartCollections.value = [];
  localStorage.clear();
});
const sent = () => JSON.parse(fetchMock.mock.lastCall![1].body as string);

it("creates a game with its tracking fields in the same request", async () => {
  await expect(createGame(input)).rejects.toThrow();
  expect(fetchMock.mock.lastCall![0]).toBe("/api/game/create");
  expect(sent()).toMatchObject({
    favorite: true,
    collections: ["Couch"],
    resume_note: "Chapter 3",
    playtime_seconds: 5400,
  });
});

it("updates tracking fields without resending an unchanged playtime", async () => {
  await expect(
    updateGame("game", { ...input, playtimeSeconds: undefined }),
  ).rejects.toThrow();
  expect(sent()).toMatchObject({
    favorite: true,
    collections: ["Couch"],
    resume_note: "Chapter 3",
  });
  expect(sent()).not.toHaveProperty("playtime_seconds");
  await expect(
    updateGame("game", { ...input, resumeNote: null, playtimeSeconds: 0 }),
  ).rejects.toThrow();
  expect(sent()).toMatchObject({ resume_note: null, playtime_seconds: 0 });
});

it("offers manual collections from games and empty saved names, never smart ones", () => {
  localStorage.setItem("manualCollections", JSON.stringify(["Empty shelf"]));
  smartCollections.value = [
    { id: "rule", name: "Long games", field: "playtime_hours", value: "40" },
  ];
  const games = [
    { ...mockGames[0]!, collections: ["Couch", "Long games"] },
    { ...mockGames[1]!, collections: ["Backlog", "Couch"] },
  ];
  expect(manualCollectionNames(games)).toEqual([
    "Backlog",
    "Couch",
    "Empty shelf",
  ]);
});
