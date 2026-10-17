import { describe, expect, it } from "vitest";
import { parseQuery, stringifyQuery } from "vue-router";
import {
  DEFAULT_GAME_FILTERS,
  hasGameLibraryQuery,
  normalizeGameTags,
  readGameLibraryFilterVisibility,
  readGameLibraryQuery,
  writeGameLibraryQuery,
  type GameLibraryFilters,
} from "../utils/gameLibraryQuery";

describe("shared game filters", () => {
  it("round trips and normalizes exclusions without changing legacy links", () => {
    const filters = readGameLibraryQuery({
      not_status: ["playing", "playing", "invalid"],
      not_tag: ["Genre: Indie", "indie"],
      not_platform: ["ps5", "PlayStation 4", "PC"],
    });
    expect(filters.excludedStatuses).toEqual(["playing"]);
    expect(filters.excludedTags.map((tag) => tag.toLowerCase())).toEqual([
      "indie",
    ]);
    expect(filters.excludedPlatforms).toEqual(["PlayStation", "PC"]);
    expect(
      readGameLibraryQuery(
        parseQuery(stringifyQuery(writeGameLibraryQuery(filters))),
      ),
    ).toEqual(filters);
    expect(readGameLibraryQuery({ tag: "Indie" }).excludedTags).toEqual([]);
  });

  it("preserves closed filters in the URL without losing selections or unrelated parameters", () => {
    const filters = { ...DEFAULT_GAME_FILTERS, tagsFilter: ["Puzzle", "RPG"] };
    const query = parseQuery(
      stringifyQuery(
        writeGameLibraryQuery(filters, { campaign: "friends" }, false),
      ),
    );
    expect(query.filters).toBe("0");
    expect(query.campaign).toBe("friends");
    expect(readGameLibraryQuery(query)).toEqual(filters);
    expect(readGameLibraryFilterVisibility(query)).toBe(false);
    expect(readGameLibraryFilterVisibility({ filters: "1" })).toBe(true);
    expect(readGameLibraryFilterVisibility({ tag: "Puzzle" })).toBe(true);
    expect(readGameLibraryFilterVisibility({})).toBe(false);
  });

  it("treats an explicit closed empty filter link as a reset of saved selections", () => {
    const query = parseQuery(
      stringifyQuery(writeGameLibraryQuery(DEFAULT_GAME_FILTERS, {}, false)),
    );
    expect(hasGameLibraryQuery(query)).toBe(true);
    expect(readGameLibraryQuery(query)).toEqual(DEFAULT_GAME_FILTERS);
    expect(readGameLibraryFilterVisibility(query)).toBe(false);
  });

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
      excludedStatuses: [],
      excludedTags: [],
      excludedPlatforms: [],
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

  it("reads tag spellings and synonyms as one canonical filter value", () => {
    expect(
      readGameLibraryQuery({
        tag: ["Steam: race", "Racing"],
        not_tag: ["Hack & Slash", "hack and slash"],
      }),
    ).toMatchObject({ tagsFilter: ["Racing"], excludedTags: ["Hack & Slash"] });
    expect(normalizeGameTags(["Roguelike", "Roguelite"])).toEqual([
      "Roguelike",
      "Roguelite",
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
