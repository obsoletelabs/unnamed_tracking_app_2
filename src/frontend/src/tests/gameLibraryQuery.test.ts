import { describe, expect, it } from "vitest";
import { parseQuery, stringifyQuery } from "vue-router";
import {
  DEFAULT_GAME_FILTERS,
  hasGameLibraryQuery,
  normalizeGameTags,
  readGameLibraryQuery,
  writeGameLibraryQuery,
  type GameLibraryFilters,
} from "../utils/gameLibraryQuery";

describe("shared game filters", () => {
  it("round trips every filter, including repeated tags and reserved characters", () => {
    const filters: GameLibraryFilters = {
      searchQuery: "Mario & Luigi",
      statusFilter: "on hold",
      platformFilter: "Nintendo Switch",
      sortBy: "rating",
      franchiseFilter: "Mario",
      collectionFilter: "Co-op & friends",
      companyFilter: "Nintendo",
      ageRatingFilter: "12+",
      regionFilter: "AU",
      languageFilter: "English",
      metadataProviderFilter: "Steam",
      favoritesOnly: true,
      achievementsFilter: "has",
      retroAchievementsOnly: true,
      missingFilter: "rating",
      tagsFilter: ["Indie", "Card & Board"],
    };
    const query = parseQuery(stringifyQuery(writeGameLibraryQuery(filters)));
    expect(query.tag).toEqual(["Indie", "Card & Board"]);
    expect(readGameLibraryQuery(query)).toEqual(filters);
  });

  it("clears stale filter keys while preserving unrelated query parameters", () => {
    const query = writeGameLibraryQuery(DEFAULT_GAME_FILTERS, {
      tag: "Indie",
      genre: "RPG",
      status: "playing",
      campaign: "friends",
    });
    expect(query).toEqual({ filters: "1", campaign: "friends" });
    expect(readGameLibraryQuery(parseQuery(stringifyQuery(query)))).toEqual(
      DEFAULT_GAME_FILTERS,
    );
    expect(hasGameLibraryQuery({ filters: "1" })).toBe(true);
    expect(hasGameLibraryQuery({ campaign: "friends" })).toBe(false);
  });

  it("accepts legacy links and drops duplicate genre labels", () => {
    expect(
      readGameLibraryQuery({
        genre: "Genre: Indie",
        tag: ["Indie", "RPG", null],
        platform: "PC",
      }).tagsFilter,
    ).toEqual(["Indie", "RPG"]);
    expect(normalizeGameTags(["Genre: Indie", "indie"], "Indie")).toEqual([
      "Indie",
    ]);
  });

  it("ignores unknown enumerations and empty query values", () => {
    expect(
      readGameLibraryQuery({
        status: "invalid",
        sort: "invalid",
        missing: "invalid",
        achievements: "invalid",
        tag: [null, ""],
        q: null,
      }),
    ).toEqual(DEFAULT_GAME_FILTERS);
  });
});
