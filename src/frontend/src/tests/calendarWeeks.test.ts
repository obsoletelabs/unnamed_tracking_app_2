import { describe, expect, it } from "vitest";
import {
  CALENDAR_AHEAD_DAYS,
  daysBetween,
  dayKey,
  insideInitialWindow,
  isWeekStart,
  monthGridRange,
  monthReachable,
  startOfWeek,
  weekdayLabels,
  type WeekStart,
} from "../utils/calendarWeeks";
import { parseEpisodeSelection } from "../utils/episodeSelection";

const STARTS: WeekStart[] = [0, 1, 2, 3, 4, 5, 6];

describe("week layout for every start day (#251)", () => {
  it("accepts exactly the seven week starts", () => {
    for (const day of STARTS) expect(isWeekStart(day)).toBe(true);
    for (const bad of [7, -1, 1.5, "1", null, undefined, NaN])
      expect(isWeekStart(bad)).toBe(false);
  });

  it("rotates the weekday headings", () => {
    expect(weekdayLabels(0)).toEqual([
      "Sun",
      "Mon",
      "Tue",
      "Wed",
      "Thu",
      "Fri",
      "Sat",
    ]);
    expect(weekdayLabels(3)).toEqual([
      "Wed",
      "Thu",
      "Fri",
      "Sat",
      "Sun",
      "Mon",
      "Tue",
    ]);
    expect(weekdayLabels(6)[0]).toBe("Sat");
  });

  it.each(STARTS)(
    "starts weeks on the chosen day (%i) and covers each date once",
    (start) => {
      // 2026-10-18 is a Sunday
      const monday = new Date(2026, 9, 19);
      const first = startOfWeek(monday, start);
      expect(first.getDay()).toBe(start);
      expect(daysBetween(first, monday)).toBeGreaterThanOrEqual(0);
      expect(daysBetween(first, monday)).toBeLessThan(7);
    },
  );

  it.each(STARTS)(
    "pads a month grid out to whole weeks beginning on day %i",
    (start) => {
      for (const [year, month] of [
        [2026, 9], // October 2026
        [2028, 1], // leap February
        [2027, 1], // non-leap February
        [2026, 11], // December: the grid runs into the next year
        [2027, 0], // January
      ]) {
        const { first, last, cells } = monthGridRange(year, month, start);
        expect(cells % 7).toBe(0);
        expect(first.getDay()).toBe(start);
        expect(daysBetween(first, last) + 1).toBe(cells);
        // the whole month is inside the grid
        expect(first <= new Date(year, month, 1)).toBe(true);
        expect(last >= new Date(year, month + 1, 0)).toBe(true);
        // and no week is entirely outside it
        expect(daysBetween(first, new Date(year, month, 1))).toBeLessThan(7);
        expect(daysBetween(new Date(year, month + 1, 0), last)).toBeLessThan(7);
      }
    },
  );

  it("handles leap days and year boundaries", () => {
    expect(dayKey(new Date(2028, 1, 29))).toBe("2028-02-29");
    const feb = monthGridRange(2028, 1, 1);
    expect(feb.cells).toBe(35); // Feb 2028 starts on a Tuesday and has 29 days
    expect(dayKey(feb.first)).toBe("2028-01-31");
    const dec = monthGridRange(2026, 11, 0);
    expect(dayKey(dec.last)).toBe("2027-01-02");
    expect(daysBetween(new Date(2026, 11, 31), new Date(2027, 0, 1))).toBe(1);
  });

  it("measures days by calendar date across a daylight-saving change", () => {
    expect(daysBetween(new Date(2026, 2, 28), new Date(2026, 3, 1))).toBe(4);
    expect(daysBetween(new Date(2026, 9, 31), new Date(2026, 10, 2))).toBe(2);
  });
});

describe("how far ahead the calendar can be browsed", () => {
  const today = new Date(2026, 9, 18);
  it("loads the first 90 days up front and everything else on demand", () => {
    expect(insideInitialWindow(new Date(2026, 11, 31), today)).toBe(true);
    expect(insideInitialWindow(new Date(2027, 0, 20), today)).toBe(false);
  });
  it("allows about a year and no more", () => {
    expect(monthReachable(2027, 8, today)).toBe(true); // Sept 2027
    expect(monthReachable(2027, 11, today)).toBe(false);
    expect(CALENDAR_AHEAD_DAYS).toBeGreaterThan(365);
  });
});

describe("which episodes were watched (#250)", () => {
  it("reads lists and ranges in any order without assuming a chronological run", () => {
    expect(parseEpisodeSelection("3, 5-7")).toEqual({
      numbers: [3, 5, 6, 7],
      label: "3, 5-7",
    });
    expect(parseEpisodeSelection("7 5 6 3")).toEqual({
      numbers: [3, 5, 6, 7],
      label: "3, 5-7",
    });
    expect(parseEpisodeSelection("2–4;4")).toEqual({
      numbers: [2, 3, 4],
      label: "2-4",
    });
  });
  it("treats an empty box as no selection and explains bad input", () => {
    expect(parseEpisodeSelection("   ")).toBeNull();
    for (const bad of ["abc", "0", "5-3", "1-", "-4", "1.5", "1-999999999"])
      expect(parseEpisodeSelection(bad)).toHaveProperty("error");
    expect(parseEpisodeSelection("1-600")).toHaveProperty("error"); // too many at once
  });
});
