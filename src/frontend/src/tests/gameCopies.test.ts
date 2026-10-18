import { describe, expect, it } from "vitest";
import { copyBadge, copyName, storeName } from "../utils/gameCopies";

describe("game copy names", () => {
  it("normalises store spellings and hides manual entries", () => {
    expect(storeName("steam")).toBe("Steam");
    expect(storeName("Epic Games Store")).toBe("Epic");
    expect(storeName("Some Plugin Store")).toBe("Some Plugin Store");
    expect(storeName("manual")).toBeNull();
    expect(storeName(null)).toBeNull();
  });

  it("names a copy by store and platform", () => {
    expect(copyName({ source: "Steam", platform: "PC (Windows)" })).toBe(
      "Steam · PC (Windows)",
    );
    expect(copyName({ source: "manual", platform: "Nintendo Switch" })).toBe(
      "Nintendo Switch",
    );
    expect(copyName({ source: null, platform: null })).toBe("Added manually");
  });

  it("labels only items that belong to another copy", () => {
    const base = { game_id: "1", title: "Hades", platform: "PC (Windows)" };
    expect(copyBadge({ ...base, source: "Steam", is_main: false })).toBe(
      "Steam · PC (Windows)",
    );
    expect(copyBadge({ ...base, source: "Epic", is_main: true })).toBeNull();
    expect(copyBadge(undefined)).toBeNull();
  });
});
