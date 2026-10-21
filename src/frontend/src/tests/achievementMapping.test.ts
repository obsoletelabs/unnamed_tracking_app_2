import { describe, expect, it } from "vitest";
import { mapAchievements } from "../services/achievementMapping";

const raw = {
  id: "a",
  provider: "Steam",
  name: "First Blood",
  description: null,
  icon_url: "/api/achievement-icon/a",
  unlocked: true,
  unlocked_at: 100,
};

describe("mapAchievements", () => {
  it("keeps what each copy of a merged game reports", () => {
    const [a] = mapAchievements([
      {
        ...raw,
        sources: [
          {
            game_id: "g1",
            copy: "Game",
            store: "Steam",
            provider: "Steam",
            unlocked: true,
            unlocked_at: 100,
          },
          {
            game_id: "g2",
            copy: "Game",
            store: "GOG",
            provider: "GOG",
            unlocked: false,
            unlocked_at: null,
          },
        ],
      },
    ]);
    expect(a.sources?.map((s) => [s.store, s.unlocked])).toEqual([
      ["Steam", true],
      ["GOG", false],
    ]);
    expect(a.sources?.[1].unlockedAt).toBeNull();
  });

  it("maps provider points, locked icons and medal tiers", () => {
    const [a] = mapAchievements([
      {
        ...raw,
        unlocked: false,
        locked_icon_url: "/api/achievement-icon/a/locked",
        points: 25,
        medal: "gold",
        tier: "missable",
      },
    ]);
    expect(a.lockedIconUrl).toBe("/api/achievement-icon/a/locked");
    expect([a.points, a.tierOverride, a.kind]).toEqual([
      25,
      "gold",
      "missable",
    ]);
    expect(a.unlockedAt).toBeNull();
  });

  it("ignores an unknown medal", () => {
    expect(
      mapAchievements([{ ...raw, medal: "platinum" }])[0].tierOverride,
    ).toBeNull();
  });
});
