import { describe, expect, it } from "vitest";
import type { Game } from "../types/game";
import type { LibraryLayout } from "../services/gameIdentity";
import {
  libraryGrouping,
  relationshipKindLabel,
  versionLabel,
} from "../utils/gameIdentityGroups";

function game(id: string, title: string, extra: Partial<Game> = {}): Game {
  return {
    id,
    title,
    parentGameId: null,
    relationshipType: null,
    platforms: [],
    source: null,
    ...extra,
  } as Game;
}

const java = game("java", "Minecraft Java Edition");
const bedrock = game("bedrock", "Minecraft Bedrock Edition");
const dungeons = game("dungeons", "Minecraft Dungeons");
const legends = game("legends", "Minecraft Legends");

function layout(overrides: Partial<LibraryLayout> = {}): LibraryLayout {
  return {
    mode: "grouped",
    rules: {},
    groups: [
      {
        umbrellaId: "java",
        playtimeSeconds: null,
        members: [
          { gameId: "bedrock", display: "grouped", relationshipId: "e1" },
        ],
      },
    ],
    related: [],
    ...overrides,
  };
}

describe("umbrella presentation", () => {
  it("shows one entry for merged versions and keeps distinct games apart", () => {
    const result = libraryGrouping(
      [bedrock, dungeons, java, legends],
      layout(),
    );
    expect(result.entries.map((g) => g.id)).toEqual([
      "dungeons",
      "java",
      "legends",
    ]);
    expect(result.versions.get("java")).toEqual([java, bedrock]);
    expect(result.members.get("java")).toEqual([java, bedrock]);
    expect(result.entryOf.get("bedrock")).toBe("bedrock");
    expect(result.versions.has("dungeons")).toBe(false);
  });

  it("lists every version on its own when the user chose separate entries", () => {
    const result = libraryGrouping(
      [java, bedrock],
      layout({ mode: "separate" }),
    );
    expect(result.entries.map((g) => g.id)).toEqual(["java", "bedrock"]);
    expect(result.versions.size).toBe(0);
  });

  it("lets a version marked separate keep its own entry", () => {
    const result = libraryGrouping(
      [java, bedrock],
      layout({
        groups: [
          {
            umbrellaId: "java",
            playtimeSeconds: null,
            members: [
              { gameId: "bedrock", display: "separate", relationshipId: "e1" },
            ],
          },
        ],
      }),
    );
    expect(result.entries.map((g) => g.id)).toEqual(["java", "bedrock"]);
  });

  it("works without a layout and never loses a version whose umbrella is gone", () => {
    expect(libraryGrouping([java, bedrock], null).entries).toHaveLength(2);
    const orphan = libraryGrouping([bedrock], layout());
    expect(orphan.entries).toEqual([bedrock]);
  });

  it("keeps owned-copy groups and folds them into the umbrella", () => {
    const copy = game("copy", "Minecraft Java Edition", {
      parentGameId: "java",
      relationshipType: "owned_copy",
    });
    const result = libraryGrouping([java, copy, bedrock], layout());
    expect(result.entries.map((g) => g.id)).toEqual(["java"]);
    expect(result.members.get("java")?.map((g) => g.id)).toEqual([
      "java",
      "copy",
      "bedrock",
    ]);
    expect(result.ownedCopies.get("java")).toBe(2);
    expect(result.versions.get("java")?.map((g) => g.id)).toEqual([
      "java",
      "bedrock",
    ]);
  });

  it("applies relationship display rules: nest, hide and link", () => {
    const related: LibraryLayout["related"] = [
      {
        id: "r1",
        kind: "dlc_of",
        label: "",
        fromId: "dungeons",
        toId: "java",
        display: "nest",
      },
      {
        id: "r2",
        kind: "related_to",
        label: "",
        fromId: "legends",
        toId: "java",
        display: "hidden",
      },
    ];
    const result = libraryGrouping(
      [java, dungeons, legends],
      layout({ related }),
    );
    expect(result.entries.map((g) => g.id)).toEqual(["java"]);
    expect(result.nested.get("java")?.map((n) => n.game.id)).toEqual([
      "dungeons",
    ]);
    const link = libraryGrouping(
      [java, dungeons],
      layout({ related: [{ ...related[0], display: "link" }] }),
    );
    expect(link.entries.map((g) => g.id)).toEqual(["java", "dungeons"]);
  });

  it("shows nested and hidden games while searching", () => {
    const related: LibraryLayout["related"] = [
      {
        id: "r1",
        kind: "dlc_of",
        label: "",
        fromId: "dungeons",
        toId: "java",
        display: "hidden",
      },
    ];
    const all = libraryGrouping([java, dungeons], layout({ related }), {
      showAll: true,
    });
    expect(all.entries.map((g) => g.id)).toEqual(["java", "dungeons"]);
  });

  it("never hides the children of a nested parent that is itself folded away", () => {
    const related: LibraryLayout["related"] = [
      {
        id: "r1",
        kind: "dlc_of",
        label: "",
        fromId: "dungeons",
        toId: "bedrock",
        display: "nest",
      },
    ];
    const result = libraryGrouping(
      [java, bedrock, dungeons],
      layout({ related }),
    );
    expect(result.entries.map((g) => g.id)).toContain("dungeons");
  });
});

describe("labels", () => {
  it("names a version by what follows the umbrella title", () => {
    expect(versionLabel(java, game("m", "Minecraft"))).toBe("Java Edition");
    expect(
      versionLabel(
        game("x", "Hades", { platforms: [{ platform: "Switch" }] as never }),
        game("h", "Hades"),
      ),
    ).toBe("Switch");
    expect(relationshipKindLabel("dlc_of")).toBe("Dlc");
    expect(relationshipKindLabel("custom", "Spiritual successor")).toBe(
      "Spiritual successor",
    );
  });
});

describe("reconciled playtime for a folded group (#277)", () => {
  const timed = (
    seconds: number | null,
    over: Partial<LibraryLayout> = {},
  ): LibraryLayout => {
    const base = layout(over);
    return {
      ...base,
      groups: base.groups.map((g) => ({ ...g, playtimeSeconds: seconds })),
    };
  };

  it("lets the entry speak for every folded copy with the page's figure", () => {
    const result = libraryGrouping([java, bedrock], timed(65 * 3600));
    expect(result.playtimeSeconds.get("java")).toBe(65 * 3600);
    expect(result.playtimeSeconds.has("bedrock")).toBe(false);
  });

  it("keeps a version shown on its own to its own hours", () => {
    const separateMode = libraryGrouping(
      [java, bedrock],
      timed(65 * 3600, { mode: "separate" }),
    );
    expect(separateMode.playtimeSeconds.size).toBe(0);
    const markedSeparate = libraryGrouping(
      [java, bedrock],
      timed(65 * 3600, {
        groups: [
          {
            umbrellaId: "java",
            playtimeSeconds: 65 * 3600,
            members: [
              { gameId: "bedrock", display: "separate", relationshipId: "e1" },
            ],
          },
        ],
      }),
    );
    expect(markedSeparate.playtimeSeconds.size).toBe(0);
  });

  it("falls back to the record when the server gave no figure", () => {
    expect(
      libraryGrouping([java, bedrock], timed(null)).playtimeSeconds.size,
    ).toBe(0);
    expect(libraryGrouping([java, bedrock], null).playtimeSeconds.size).toBe(0);
  });

  it("folds owned copies too, so Steam and Epic hours add up on the card", () => {
    const steam = game("steam", "Hades", { platforms: [] });
    const epic = game("epic", "Hades", {
      parentGameId: "steam",
      relationshipType: "owned_copy",
    });
    const copies: LibraryLayout = {
      mode: "grouped",
      rules: {},
      related: [],
      groups: [
        {
          umbrellaId: "steam",
          playtimeSeconds: 65 * 3600,
          members: [
            { gameId: "epic", display: "grouped", relationshipId: null },
          ],
        },
      ],
    };
    const result = libraryGrouping([steam, epic], copies);
    expect(result.entries.map((g) => g.id)).toEqual(["steam"]);
    expect(result.playtimeSeconds.get("steam")).toBe(65 * 3600);
  });
});
