import { afterEach, describe, expect, it, vi } from "vitest";
import {
  createManualAchievement,
  fetchCompletion,
  setCompletion,
  setManualAchievementEarned,
} from "../services/gameTracking";

function respond(body: unknown, ok = true, status = 200): Response {
  return {
    ok,
    status,
    statusText: "",
    json: async () => body,
    text: async () => JSON.stringify(body),
    headers: new Headers({ "content-type": "application/json" }),
  } as unknown as Response;
}

afterEach(() => vi.unstubAllGlobals());

describe("manual tracking service", () => {
  it("maps partial completion across versions", async () => {
    const fetchMock = vi.fn(async () =>
      respond({
        completed: 1,
        of: 2,
        partial: true,
        all_completed: false,
        summary: "Completed on 1 of 2 tracked versions",
        versions: [
          {
            game_id: "a",
            title: "PC",
            platform: "PC",
            status: "BEATEN",
            completed: true,
            user_set: true,
          },
          {
            game_id: "b",
            title: "Deck",
            platform: null,
            status: "BACKLOG",
            completed: false,
            user_set: false,
          },
        ],
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const result = await fetchCompletion("a");
    expect(result.partial).toBe(true);
    expect(result.versions.map((v) => [v.gameId, v.userSet])).toEqual([
      ["a", true],
      ["b", false],
    ]);
    expect(fetchMock.mock.calls[0]).toEqual([
      "/api/game-identity/games/a/completion",
      expect.objectContaining({ method: "GET", credentials: "include" }),
    ]);
  });

  it("sends snake_case bodies for completion and achievements", async () => {
    const fetchMock = vi.fn(async (_url: string, init?: RequestInit) =>
      init?.method === "PUT" && String(_url).endsWith("/completion")
        ? respond({
            completed: 0,
            of: 1,
            partial: false,
            all_completed: false,
            summary: "",
            versions: [],
          })
        : respond({
            id: "m",
            scope: "version",
            game_id: "a",
            title: "T",
            description: null,
            icon_url: null,
            category: null,
            position: 0,
            achieved: true,
            achieved_at: 5,
            match_key: null,
          }),
    );
    vi.stubGlobal("fetch", fetchMock);
    await setCompletion("a", "BEATEN");
    expect(JSON.parse(fetchMock.mock.calls[0][1]!.body as string)).toEqual({
      status: "BEATEN",
    });
    const made = await createManualAchievement("a", {
      title: "T",
      iconUrl: "https://x.test/i.png",
    });
    expect(made.achieved).toBe(true);
    expect(
      JSON.parse(fetchMock.mock.calls[1][1]!.body as string),
    ).toMatchObject({
      title: "T",
      icon_url: "https://x.test/i.png",
      scope: "version",
    });
    await setManualAchievementEarned("m", false);
    expect(fetchMock.mock.calls[2][0]).toBe(
      "/api/game-identity/manual-achievements/m/earned",
    );
  });

  it("surfaces a rejected request as an error", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => respond({ detail: "Game not found" }, false, 404)),
    );
    await expect(fetchCompletion("x")).rejects.toThrow();
  });
});
