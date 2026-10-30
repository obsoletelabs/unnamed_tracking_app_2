import { afterEach, describe, expect, it } from "vitest";

import {
  canonicalTag,
  gameGenres,
  genreOptionsFor,
  hasGenre,
  sameTag,
  setCatalogueRules,
  tagFilterOptions,
  tagKey,
} from "../utils/genres";

describe("gameGenres", () => {
  it("treats provider-prefixed genres as the same picker choice", () => {
    expect(gameGenres(["Genre: Indie", "Indie"])).toEqual(["Indie"]);
    expect(hasGenre(["Genre: Indie"], "Indie")).toBe(true);
    expect(hasGenre(["Indie"], "Genre: Indie")).toBe(true);
  });
  it("keeps the provider's genre and adds the shared ones it stands for", () => {
    expect(gameGenres(["Hack and slash/Beat 'em up", "Adventure"])).toEqual([
      "Hack and slash/Beat 'em up",
      "Action",
      "Adventure",
    ]);
    expect(gameGenres(["Devil May Cry", "Twin Stick Shooter"])).toEqual([
      "Devil May Cry",
      "Twin Stick Shooter",
      "Shooter",
      "Action",
    ]);
  });

  it("reads another provider's name for a shared genre as that genre", () => {
    expect(gameGenres(["Role-playing (RPG)"])).toEqual(["RPG"]);
    expect(gameGenres(["Platform", "Simulator"])).toEqual([
      "Platformer",
      "Simulation",
    ]);
  });

  it("spells shared genres one way and drops duplicates", () => {
    expect(gameGenres(["action", "Action", " ACTION "])).toEqual(["Action"]);
  });

  it("leaves genres it doesn't know as they are", () => {
    expect(gameGenres(["Indie", "Nintendo 64"])).toEqual([
      "Indie",
      "Nintendo 64",
    ]);
  });
});

describe("hasGenre", () => {
  it("matches across providers and letter case", () => {
    expect(hasGenre(["Shooter"], "Action")).toBe(true);
    expect(hasGenre(["Platform"], "platformer")).toBe(true);
    expect(hasGenre(["Puzzle"], "Action")).toBe(false);
    expect(hasGenre([], "Action")).toBe(false);
  });
});

describe("genreOptionsFor", () => {
  it("offers each genre once, shared spelling first", () => {
    const options = genreOptionsFor([["action", "Indie"], ["Indie"]]);
    expect(options.filter((o) => o.toLowerCase() === "action")).toEqual([
      "Action",
    ]);
    expect(options.filter((o) => o === "Indie")).toHaveLength(1);
    expect(options).toContain("RPG");
  });
});

describe("tag normalization", () => {
  it("ignores case, accents, spacing, punctuation and known source labels", () => {
    expect(sameTag("Rogue-like", "roguelike")).toBe(true);
    expect(sameTag("Steam: Roguelike", "Roguelike")).toBe(true);
    expect(sameTag("Hack & Slash", "Hack and Slash")).toBe(true);
    expect(sameTag("Pokémon", "pokemon")).toBe(true);
    expect(sameTag("  Turn-Based  ", "turn based")).toBe(true);
    expect(sameTag("Tag: Genre: Indie", "indie")).toBe(true);
  });

  it("keeps semantically different tags apart", () => {
    expect(sameTag("Roguelike", "Roguelite")).toBe(false);
    expect(sameTag("Souls-like", "Action RPG")).toBe(false);
    expect(sameTag("Driving", "Racing")).toBe(false);
    expect(hasGenre(["Roguelite"], "Roguelike")).toBe(false);
    expect(hasGenre(["Walking Simulator"], "Simulation")).toBe(false);
    // a colon inside an ordinary tag is not a source label
    expect(canonicalTag("Warhammer: 40K")).toBe("Warhammer: 40K");
  });

  it("merges the issue's racing spellings into one choice", () => {
    for (const tag of ["racing", "Race", "Steam: race", "RACING"])
      expect(canonicalTag(tag)).toBe("Racing");
    expect(hasGenre(["Steam: race"], "Racing")).toBe(true);
    expect(sameTag("Science Fiction", "Sci-fi")).toBe(true);
    expect(sameTag("FPS", "First-Person Shooter")).toBe(true);
  });

  it("keeps tags in non-Latin scripts findable", () => {
    expect(tagKey("ロールプレイング")).not.toBe("");
    expect(gameGenres(["ロールプレイング"])).toEqual(["ロールプレイング"]);
    expect(tagKey("!!!")).toBe("");
  });
});

describe("tagFilterOptions", () => {
  const library = [
    ["Racing", "Steam"],
    ["racing", "Steam: race", "Steam"],
    ["Race"],
    ["Hack & Slash", "Turn-Based"],
    ["Hack and Slash", "Turn-Based"],
    ["Turn-based", "Roguelite"],
  ];

  it("offers one choice per tag with game counts, shared genres first", () => {
    const options = tagFilterOptions(library);
    expect(options.map((o) => [o.value, o.count, o.shared])).toEqual([
      ["Action", 2, true],
      ["Racing", 3, true],
      ["Turn-Based", 3, false],
      ["Hack & Slash", 2, false],
      ["Steam", 2, false],
      ["Roguelite", 1, false],
    ]);
  });

  it("says which spellings and broader genres a choice covers", () => {
    const options = tagFilterOptions(library);
    const find = (value: string) => options.find((o) => o.value === value)!;
    expect(find("Racing").spellings).toEqual(["Race", "Steam: race"]);
    expect(find("Hack & Slash")).toMatchObject({
      spellings: ["Hack and Slash"],
      broader: ["Action"],
    });
    expect(find("Turn-Based").spellings).toEqual([]);
  });

  it("stays deterministic whatever order the library arrives in", () => {
    const forward = tagFilterOptions(library);
    const backward = tagFilterOptions([...library].reverse());
    expect(backward).toEqual(forward);
  });
});

describe("tag catalogue rules", () => {
  afterEach(() => setCatalogueRules({}));

  it("groups a mapped spelling under its catalogue label without touching stored tags", () => {
    const stored = ["Roguelite", "Indie"];
    setCatalogueRules({ roguelite: "Roguelike" });
    expect(sameTag("roguelite", "Roguelike")).toBe(true);
    expect(canonicalTag("  ROGUELITE ")).toBe("Roguelike");
    expect(gameGenres(stored)).toEqual(["Roguelike", "Indie"]);
    expect(stored).toEqual(["Roguelite", "Indie"]);
  });

  it("merges the picker choices and lists the mapped spelling", () => {
    setCatalogueRules({ roguelite: "Roguelike" });
    const options = tagFilterOptions([["Roguelike"], ["Roguelite"]]);
    const choice = options.find((o) => o.value === "Roguelike")!;
    expect(choice.count).toBe(2);
    expect(choice.spellings).toEqual(["Roguelite"]);
  });

  it("keeps related tags separate again once the rule is gone", () => {
    setCatalogueRules({ roguelite: "Roguelike" });
    setCatalogueRules({});
    expect(sameTag("Roguelite", "Roguelike")).toBe(false);
  });
});
