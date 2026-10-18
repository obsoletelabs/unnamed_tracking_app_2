import { describe, expect, it } from "vitest";
import {
  compareBySearchMatch,
  searchMatchRank,
  SEARCH_MATCH_RANK,
} from "../utils/searchMatchRank";

describe("search match ranking", () => {
  it("ranks exact > prefix > word start > substring > fuzzy", () => {
    expect(searchMatchRank("Portal", "portal")).toBe(SEARCH_MATCH_RANK.exact);
    expect(searchMatchRank("Portal 2", "portal")).toBe(
      SEARCH_MATCH_RANK.prefix,
    );
    expect(searchMatchRank("Bridge Constructor: Portal", "portal")).toBe(
      SEARCH_MATCH_RANK.wordStart,
    );
    expect(searchMatchRank("Teleportals", "portal")).toBe(
      SEARCH_MATCH_RANK.substring,
    );
    expect(searchMatchRank("Portel", "portal")).toBe(SEARCH_MATCH_RANK.fuzzy);
  });

  it("orders by best match and leaves ties to the existing sort", () => {
    const titles = [
      "Teleportals",
      "Bridge Constructor: Portal",
      "Portal 2",
      "Portal Knights",
      "Portal",
    ];
    const sorted = [...titles].sort(
      (a, b) =>
        compareBySearchMatch([a], [b], " Portal ") || b.localeCompare(a),
    );
    expect(sorted).toEqual([
      "Portal",
      "Portal Knights",
      "Portal 2",
      "Bridge Constructor: Portal",
      "Teleportals",
    ]);
  });

  it("uses the best of several titles", () => {
    expect(
      compareBySearchMatch(["Teleportals", "Portal"], ["Portal 2"], "portal"),
    ).toBeLessThan(0);
  });
});
