import { describe, expect, it } from "vitest";
import { titleMatchRank } from "../utils/searchRelevance";
import {
  readGameLibraryQuery,
  GAME_SORT_KEYS,
} from "../utils/gameLibraryQuery";
import { MEDIA_SORT_KEYS } from "../types/mediaLibrary";

describe("best match ordering (#245)", () => {
  it("ranks exact, prefix, word-prefix and contained matches in that order", () => {
    const rank = (title: string) => titleMatchRank([title], "hunt");
    expect(rank("Hunt")).toBe(0);
    expect(rank("Hunters of the Dawn")).toBe(1);
    expect(rank("A Hunt for Red October")).toBe(2);
    expect(rank("Manhunt")).toBe(3);
    expect(rank("Unrelated")).toBe(4);
  });

  it("is case-insensitive and uses the best of several titles or copies", () => {
    expect(titleMatchRank(["Hades II", "HADES"], "  hades ")).toBe(0);
    expect(titleMatchRank(["Super Hades"], "hades")).toBe(2);
    expect(titleMatchRank(["Anything"], "")).toBe(4);
  });

  it("is a sort choice the library link and saved preference can carry", () => {
    expect(GAME_SORT_KEYS).toContain("relevance");
    expect(MEDIA_SORT_KEYS).toContain("relevance");
    expect(readGameLibraryQuery({ sort: "relevance", q: "hunt" }).sortBy).toBe(
      "relevance",
    );
  });
});
