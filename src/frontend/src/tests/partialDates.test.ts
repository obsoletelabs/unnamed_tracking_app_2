// C5.5: a release date is shown as precisely as it is known, in the person's
// date style, and lists sort a partial date as the start of its period.
import { describe, expect, it } from "vitest";
import {
  compareReleaseDates,
  formatDisplayDate,
  formatPartialDate,
} from "../utils/dates";
import type { DateFormatPreferences } from "../utils/dates";
import { formatReleaseDate, newestRelease } from "../utils/releaseDate";
import { preferences } from "../state/preferences";
import { DEFAULT_PREFERENCES } from "../services/preferences";

const locale = (style: "short" | "long"): DateFormatPreferences => ({
  date_display: "locale",
  partial_date_style: style,
});
const iso: DateFormatPreferences = {
  date_display: "iso",
  partial_date_style: "long",
};
const monthOf = (month: "long" | "short") =>
  new Date(2011, 3, 1).toLocaleDateString(undefined, {
    year: "numeric",
    month,
  });

describe("formatPartialDate", () => {
  describe.each(["long", "short"] as const)("in the %s style", (style) => {
    it("shows a year as the year alone, never its first day", () => {
      const shown = formatPartialDate("2011-01-01", "year", locale(style));
      expect(shown).toBe("2011");
      expect(shown).not.toBe(formatDisplayDate("2011-01-01"));
    });

    it("shows a month as its name and year, without a day", () => {
      const shown = formatPartialDate("2011-04-01", "month", locale(style));
      expect(shown).toBe(monthOf(style));
      expect(shown).toContain("2011");
      expect(shown).not.toMatch(/(^|\D)1(\D|$)/);
    });

    it("shows a day (no precision, or day) as the existing day format", () => {
      expect(formatPartialDate("2011-04-19", null, locale(style))).toBe(
        formatDisplayDate("2011-04-19"),
      );
      expect(formatPartialDate("2011-04-19", "day", locale(style))).toBe(
        formatDisplayDate("2011-04-19"),
      );
    });
  });

  it("uses the long month name by default and the short one when asked", () => {
    if (new Intl.DateTimeFormat().resolvedOptions().locale.startsWith("en")) {
      expect(formatPartialDate("2011-04-01", "month", locale("long"))).toBe(
        "April 2011",
      );
      expect(formatPartialDate("2011-04-01", "month", locale("short"))).toBe(
        "Apr 2011",
      );
    }
    expect(monthOf("long")).not.toBe(monthOf("short"));
  });

  it("shows the stored value as written in the iso style, for each precision", () => {
    expect(formatPartialDate("2011-01-01", "year", iso)).toBe("2011");
    expect(formatPartialDate("2011-04-01", "month", iso)).toBe("2011-04");
    expect(formatPartialDate("2011-04-19", null, iso)).toBe("2011-04-19");
    expect(formatPartialDate("2011-04-19", "day", iso)).toBe("2011-04-19");
    // a partial value as Atlas writes it
    expect(formatPartialDate("2011-04", "month", iso)).toBe("2011-04");
  });

  it("reads the precision from a partial value's shape", () => {
    expect(formatPartialDate("2011", null, locale("long"))).toBe("2011");
    expect(formatPartialDate("2011-04", null, locale("short"))).toBe(
      monthOf("short"),
    );
  });

  it("is empty without a date", () => {
    expect(formatPartialDate(null, "year", locale("long"))).toBe("");
    expect(formatPartialDate("", null, iso)).toBe("");
  });
});

describe("compareReleaseDates", () => {
  it("puts a year before a day of the same year, and a month between", () => {
    const dates = [
      { date: "2011-04-19", precision: null },
      { date: "2011-01-01", precision: "day" as const },
      { date: "2011-04-01", precision: "month" as const },
      { date: "2011-01-01", precision: "year" as const },
      { date: "2010-12-31", precision: null },
    ];
    expect(
      [...dates].sort(compareReleaseDates).map((d) => [d.date, d.precision]),
    ).toEqual([
      ["2010-12-31", null],
      ["2011-01-01", "year"],
      ["2011-01-01", "day"],
      ["2011-04-01", "month"],
      ["2011-04-19", null],
    ]);
  });
});

describe("formatReleaseDate", () => {
  it("follows the signed-in person's date preferences", () => {
    const game = {
      releaseDate: "2011-04-01",
      releaseDatePrecision: "month" as const,
    };
    preferences.value = { ...DEFAULT_PREFERENCES, date_display: "iso" };
    expect(formatReleaseDate(game)).toBe("2011-04");
    preferences.value = { ...DEFAULT_PREFERENCES, partial_date_style: "short" };
    expect(formatReleaseDate(game)).toBe(monthOf("short"));
    preferences.value = { ...DEFAULT_PREFERENCES };
    expect(
      formatReleaseDate({
        releaseDate: "2027-01-01",
        releaseDatePrecision: "year",
      }),
    ).toBe("2027");
  });
});

describe("newestRelease", () => {
  it("orders the library newest first, a day ahead of its own year, missing dates last", () => {
    const games = [
      { id: "none", releaseDate: null, releaseDatePrecision: null },
      {
        id: "year",
        releaseDate: "2011-01-01",
        releaseDatePrecision: "year" as const,
      },
      { id: "day", releaseDate: "2011-04-19", releaseDatePrecision: null },
      {
        id: "month",
        releaseDate: "2011-04-01",
        releaseDatePrecision: "month" as const,
      },
      { id: "older", releaseDate: "2010-06-01", releaseDatePrecision: null },
    ];
    expect([...games].sort(newestRelease).map((g) => g.id)).toEqual([
      "day",
      "month",
      "year",
      "older",
      "none",
    ]);
  });
});
