import { afterEach, describe, expect, it, vi } from "vitest";
import {
  fetchNameReviews,
  keepGameName,
  repairGameNames,
  updateGameName,
} from "../services/gameNames";

function reply(body: unknown, ok = true) {
  return vi.fn().mockResolvedValue({
    ok,
    status: ok ? 200 : 404,
    json: async () => body,
    text: async () => JSON.stringify(body),
  });
}

afterEach(() => vi.unstubAllGlobals());

describe("game name review client", () => {
  it("lists open reviews for the signed-in user", async () => {
    const fetchMock = reply([{ id: "r1" }]);
    vi.stubGlobal("fetch", fetchMock);
    expect(await fetchNameReviews()).toEqual([{ id: "r1" }]);
    expect(fetchMock.mock.calls[0][0]).toBe("/api/game-identity/names/reviews");
  });

  it("answers Update and Keep current on the review", async () => {
    const fetchMock = reply({ title: "Portal 2" });
    vi.stubGlobal("fetch", fetchMock);
    await updateGameName("r1");
    await keepGameName("r2");
    expect(fetchMock.mock.calls.map((call) => call[0])).toEqual([
      "/api/game-identity/names/reviews/r1/update",
      "/api/game-identity/names/reviews/r2/keep",
    ]);
    expect(fetchMock.mock.calls[0][1].method).toBe("POST");
  });

  it("requests a repair, optionally as a dry run", async () => {
    const fetchMock = reply({ checked: 1 });
    vi.stubGlobal("fetch", fetchMock);
    await repairGameNames(true);
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({
      dry_run: true,
    });
  });

  it("raises when the request fails", async () => {
    vi.stubGlobal("fetch", reply({ detail: "No open name change" }, false));
    await expect(keepGameName("gone")).rejects.toBeInstanceOf(Error);
  });
});
