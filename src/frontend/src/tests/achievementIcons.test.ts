import { describe, expect, it } from "vitest";
import { iconOf } from "../utils/achievements";
import type { Achievement } from "../types/game";

const base: Achievement = {
  id: "a",
  name: "Won",
  unlockedAt: null,
  unlocked: false,
  iconUrl: "/api/achievement-icon/a",
  lockedIconUrl: "/api/achievement-icon/a/locked",
};

describe("iconOf", () => {
  it("shows the provider's locked icon while it is locked", () => {
    expect(iconOf(base)).toBe("/api/achievement-icon/a/locked");
  });
  it("shows the normal icon once unlocked", () => {
    expect(iconOf({ ...base, unlocked: true })).toBe("/api/achievement-icon/a");
  });
  it("falls back to the normal icon, or nothing", () => {
    expect(iconOf({ ...base, lockedIconUrl: null })).toBe(
      "/api/achievement-icon/a",
    );
    expect(iconOf({ ...base, iconUrl: null, lockedIconUrl: null })).toBeNull();
  });
});
