import { describe, expect, it } from "vitest";

import {
  formatProgressMinutes,
  parseProgressMinutes,
  progressPercent,
} from "../utils/watchProgress";

describe("movie left-off point (#191)", () => {
  it("accepts minutes or h:mm", () => {
    expect(parseProgressMinutes("72")).toBe(72);
    expect(parseProgressMinutes("1:12")).toBe(72);
    expect(parseProgressMinutes(" 0:05 ")).toBe(5);
    expect(parseProgressMinutes("")).toBeNull();
    expect(parseProgressMinutes("an hour")).toBeNaN();
    expect(parseProgressMinutes("1:75")).toBeNaN();
  });

  it("formats as h:mm", () => {
    expect(formatProgressMinutes(72)).toBe("1:12");
    expect(formatProgressMinutes(5)).toBe("0:05");
    expect(formatProgressMinutes(parseProgressMinutes("2:28")!)).toBe("2:28");
  });

  it.each([
    ["1h12m", 72],
    ["1h 12", 72],
    [" 1 HOUR 12 minutes ", 72],
    ["2hrs", 120],
    ["45m", 45],
    ["90 mins", 90],
    ["1.5h", 90],
    ["1.01 hours", 61],
    ["0h", 0],
  ])("accepts time text %s as %i minutes", (text, minutes) => {
    expect(parseProgressMinutes(text)).toBe(minutes);
  });

  it.each([
    "1h75m",
    "1.5h30m",
    "-1h",
    "Infinity",
    "2147483648",
    "35791394:08",
    "35791394h8m",
    "999999999999999999999h",
    "9".repeat(400),
    "0".repeat(65) + "72",
    "1" + " ".repeat(1000) + "h",
  ])("rejects invalid or unrepresentable time %s", (text) => {
    expect(parseProgressMinutes(text)).toBeNaN();
  });

  it("accepts the largest stored minute value in every format", () => {
    for (const text of ["2147483647", "35791394:07", "35791394h7m"]) {
      expect(parseProgressMinutes(text)).toBe(2147483647);
    }
  });

  it("reports progress against the runtime, capped at 100%", () => {
    expect(progressPercent(74, 148)).toBe(50);
    expect(progressPercent(200, 148)).toBe(100);
    expect(progressPercent(30, null)).toBeNull();
    expect(progressPercent(null, 148)).toBeNull();
  });
});
