// @vitest-environment happy-dom
// Game page presentation helpers: release rows at their known precision,
// the "Released" fact, provider medals and the Plugin Manager status words.
import { describe, expect, it } from "vitest";
import { ref } from "vue";
import { useGameAchievements } from "../composables/useGameAchievements";
import type { Achievement, Game } from "../types/game";
import { mapReleaseFields } from "../services/gameApiTypes";
import type { DateFormatPreferences } from "../utils/dates";
import { regionName, releaseFacts, releaseRows } from "../utils/releaseDate";
import { isPlatinum, MEDAL_LABEL } from "../utils/achievements";
import {
  healthTone,
  pluginStatusLabel,
  pluginStatusTone,
  trustLabel,
  trustTone,
} from "../utils/pluginStatus";

const iso: DateFormatPreferences = {
  date_display: "iso",
  partial_date_style: "long",
};

describe("release rows", () => {
  const releases = mapReleaseFields({
    release_date_precision: null,
    releases: [
      {
        date: "2016-05",
        precision: "month",
        platform: "pc",
        region: "ww",
        edition: "Game of the Year Edition",
        category: "rerelease",
      },
      {
        date: "2015-09-21",
        precision: "day",
        platform: "pc",
        region: "eu",
        category: "full",
      },
      {
        date: "2016",
        precision: "year",
        platform: "ps4",
        region: "jp",
        category: "port",
      },
      {
        date: "2015-03",
        precision: "month",
        platform: "pc",
        region: "ww",
        category: "full",
      },
    ],
  }).releases;

  it("lists them oldest first, each no more precise than known", () => {
    expect(releaseRows(releases, iso).map((r) => r.date)).toEqual([
      "2015-03",
      "2015-09-21",
      "2016",
      "2016-05",
    ]);
  });

  it("names the platform, region, edition and a non-full category", () => {
    const rows = releaseRows(releases, iso);
    expect(rows[0]).toMatchObject({
      where: "PC · Worldwide",
      edition: null,
      category: null,
    });
    expect(rows[1].where).toBe("PC · Europe");
    expect(rows[2].category).toBe("Port");
    expect(rows[3]).toMatchObject({
      edition: "Game of the Year Edition",
      category: "Re-release",
    });
  });

  it("names a country code and keeps an unknown one readable", () => {
    expect(regionName("ww")).toBe("Worldwide");
    expect(regionName("latam")).toBe("Latin America");
    expect(regionName("jp")).not.toBe("");
    expect(regionName(null)).toBeNull();
  });

  it("maps a record without releases to an empty list", () => {
    expect(mapReleaseFields({ release_date_precision: "year" })).toEqual({
      releaseDatePrecision: "year",
      releases: [],
    });
  });
});

describe("the Released fact", () => {
  it("shows a year-only date as the year", () => {
    expect(
      releaseFacts({ releaseDate: "2011-01-01", releaseDatePrecision: "year" }),
    ).toEqual([{ label: "Released", value: "2011", field: "release_date" }]);
  });

  it("is absent without a release date", () => {
    expect(releaseFacts({ releaseDate: null })).toEqual([]);
  });
});

describe("achievement medals", () => {
  it("labels every medal and recognises platinum", () => {
    expect(MEDAL_LABEL.platinum).toBe("Platinum");
    expect(
      isPlatinum({
        id: "a",
        name: "All",
        unlockedAt: null,
        tierOverride: "platinum",
      }),
    ).toBe(true);
    expect(
      isPlatinum({
        id: "b",
        name: "Gold",
        unlockedAt: null,
        tierOverride: "gold",
      }),
    ).toBe(false);
  });
});

describe("plugin status words", () => {
  it("reads runtime states and colours failures", () => {
    expect(pluginStatusLabel("running")).toBe("Running");
    expect(pluginStatusLabel("failed_start")).toBe("Failed to start");
    expect(pluginStatusLabel("healthy")).toBe("Healthy");
    expect(pluginStatusTone({ status: "running", health: "healthy" })).toBe(
      "good",
    );
    expect(pluginStatusTone({ status: "running", health: "degraded" })).toBe(
      "warn",
    );
    expect(
      pluginStatusTone({ status: "failed_start", health: "unknown" }),
    ).toBe("danger");
    expect(pluginStatusTone({ status: "stopped", health: "unknown" })).toBe(
      "neutral",
    );
    expect(healthTone("unhealthy")).toBe("danger");
  });

  it("names publisher trust and flags anything unverified", () => {
    expect(
      trustLabel({ status: "trusted", publisher_channel: "official" }),
    ).toBe("Official · verified");
    expect(trustLabel({ status: "unsigned" })).toBe("Unsigned");
    expect(trustLabel(undefined)).toBe("Unverified");
    expect(trustTone({ status: "trusted" })).toBe("good");
    expect(trustTone({ status: "unsigned" })).toBe("warn");
    expect(trustTone({ status: "invalid_signature" })).toBe("danger");
  });
});

describe("the achievement summary", () => {
  const ach = (over: Partial<Achievement>): Achievement => ({
    id: over.name ?? "a",
    name: "a",
    unlockedAt: null,
    unlocked: false,
    ...over,
  });
  const statsFor = (achievements: Achievement[]) =>
    useGameAchievements(
      ref({ id: "g1", achievements } as unknown as Game),
    ).achStats.value.map((s) => `${s.value} ${s.label}`);

  it("says whether the platinum is earned", () => {
    expect(
      statsFor([
        ach({ name: "All", tierOverride: "platinum" }),
        ach({ name: "b" }),
      ]),
    ).toContain("Platinum not earned yet");
    expect(
      statsFor([
        ach({
          name: "All",
          tierOverride: "platinum",
          unlocked: true,
          unlockedAt: "2026-11-07T10:00:00Z",
        }),
      ]),
    ).toContain("Platinum earned");
  });

  it("adds no platinum figure to a game without one", () => {
    expect(
      statsFor([ach({ name: "x", tierOverride: "gold" })]).join(),
    ).not.toContain("Platinum");
  });
});
