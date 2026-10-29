import { describe, expect, it } from "vitest";
import {
  cameFromPlaynite,
  originSentence,
  reporterName,
  reporterNames,
  sameCopyBlocker,
  type Reporter,
} from "../utils/gameOrigin";

const playnite: Reporter = {
  kind: "host",
  source: "playnite",
  label: "Playnite",
  disconnected: false,
};
const epicPlugin: Reporter = {
  kind: "plugin",
  source: "epic-games",
  label: "Epic Games",
  disconnected: false,
};
const steam: Reporter = {
  kind: "host",
  source: "steam",
  label: "Steam",
  disconnected: false,
};

describe("who reported a game record", () => {
  it("names Playnite, plugins and store syncs differently", () => {
    expect(reporterName(playnite)).toBe("Playnite");
    expect(reporterName(epicPlugin)).toBe("Epic Games plugin");
    expect(reporterName(steam)).toBe("Steam sync");
  });

  it("lists Playnite first and each reporter once", () => {
    expect(
      reporterNames({
        source: "Epic",
        platform: null,
        reported_by: [epicPlugin, playnite, playnite],
      }),
    ).toEqual(["Playnite", "Epic Games plugin"]);
  });

  it("tells a Playnite record of an Epic game from the plugin's", () => {
    const viaPlaynite = {
      source: "Epic",
      platform: "PC (Windows)",
      reported_by: [playnite],
    };
    const viaPlugin = {
      source: "Epic Games",
      platform: "PC (Windows)",
      reported_by: [epicPlugin],
    };
    expect(cameFromPlaynite(viaPlaynite)).toBe(true);
    expect(cameFromPlaynite(viaPlugin)).toBe(false);
    expect(originSentence(viaPlaynite)).toBe(
      "Epic · PC (Windows), from Playnite",
    );
    expect(originSentence(viaPlugin)).toBe(
      "Epic · PC (Windows), from Epic Games plugin",
    );
  });

  it("falls back to the store alone when nothing reported it", () => {
    expect(originSentence({ source: "manual", platform: "Android" })).toBe(
      "Android",
    );
  });
});

describe("same copy or different copies", () => {
  it("treats one store under two names as the same copy", () => {
    expect(
      sameCopyBlocker(
        { source: "Epic", platform: "PC (Windows)" },
        { source: "Epic Games", platform: "pc (windows)" },
      ),
    ).toBeNull();
  });

  it("never treats another store or platform as the same copy", () => {
    expect(
      sameCopyBlocker(
        { source: "Epic", platform: null },
        { source: "Steam", platform: null },
      ),
    ).toContain("different stores");
    expect(
      sameCopyBlocker(
        { source: "Steam", platform: "PC" },
        { source: "Steam", platform: "Linux" },
      ),
    ).toContain("different platforms");
  });
});
