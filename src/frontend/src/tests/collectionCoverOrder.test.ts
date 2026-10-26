import { describe, expect, it } from "vitest";
import {
  collectionCoverUrl,
  orderCollectionCoverGames,
} from "../services/collectionCovers";

describe("collection cover ordering", () => {
  const games = ["a", "b", "c", "d", "e"].map((id) => ({ id }));

  it("uses manual order for the first four and keeps new games after it", () => {
    expect(
      orderCollectionCoverGames(games, ["d", "b", "a", "c"], undefined)
        .slice(0, 4)
        .map((game) => game.id),
    ).toEqual(["d", "b", "a", "c"]);
  });

  it("retains the picked cover as the first tile", () => {
    expect(
      orderCollectionCoverGames(games, ["d", "b", "a", "c"], "e")
        .slice(0, 4)
        .map((game) => game.id),
    ).toEqual(["e", "d", "b", "a"]);
  });

  it("requests one image with four ordered game IDs", () => {
    const url = new URL(
      collectionCoverUrl("My Shelf", ["d", "b", "a", "c", "e"]),
      "http://test",
    );
    expect(url.pathname).toBe("/api/game/collection-cover");
    expect(url.searchParams.get("name")).toBe("My Shelf");
    expect(url.searchParams.getAll("ids")).toEqual(["d", "b", "a", "c"]);
  });
});
