// A date-only value ("2026-09-12", what <input type="date"> and the API's
// date fields use) is a calendar day, not an instant. `new Date("2026-09-12")`
// reads it as UTC midnight, which toLocaleDateString() then shows as the day
// before anywhere west of UTC. These helpers keep calendar days as local
// days on the way in and out.

const DATE_ONLY = /^(\d{4})-(\d{2})-(\d{2})$/;

// For display: date-only strings become that local calendar day, anything
// else (a full ISO timestamp) is parsed as usual.
export function parseDisplayDate(value: string): Date {
  const match = DATE_ONLY.exec(value);
  if (!match) return new Date(value);
  return new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
}

export function formatDisplayDate(
  value: string | null | undefined,
  options?: Intl.DateTimeFormatOptions,
): string {
  if (!value) return "";
  return parseDisplayDate(value).toLocaleDateString(undefined, options);
}

// "YYYY-MM-DD" of the local calendar day an instant falls on, the value an
// <input type="date"> needs.
export function toLocalDateInput(
  value: string | Date | null | undefined,
): string {
  if (!value) return "";
  if (typeof value === "string" && DATE_ONLY.test(value)) return value;
  const date = typeof value === "string" ? new Date(value) : value;
  if (Number.isNaN(date.getTime())) return "";
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

// Unix seconds at local midnight of a "YYYY-MM-DD" day, so it reads back
// (toLocalDateInput / toLocaleDateString) as the same day that was picked.
export function localDateInputToUnixSeconds(value: string): number | null {
  const match = DATE_ONLY.exec(value);
  if (!match) return null;
  const date = new Date(
    Number(match[1]),
    Number(match[2]) - 1,
    Number(match[3]),
  );
  return Math.floor(date.getTime() / 1000);
}

/** Orders newest first; a missing date sorts last. Accepts ISO strings or unix seconds. */
export function newest(
  a: string | number | null | undefined,
  b: string | number | null | undefined,
): number {
  const time = (v: string | number | null | undefined) =>
    typeof v === "number" ? v * 1000 : Date.parse(v ?? "") || 0;
  return time(b) - time(a);
}

// How much of a release date is known (CONTRACTS-v1 C5). The API sends a year
// or a month as the first day of its period plus this precision; a day has no
// precision (null), as every date stored before partial dates existed.
export type DatePrecision = "year" | "month" | "day";

export interface DateFormatPreferences {
  // "locale": the browser's language; "iso": the stored value as written
  date_display: "locale" | "iso";
  // a month as "April 2011" (long) or "Apr 2011" (short)
  partial_date_style: "short" | "long";
}

const PARTIAL = /^(\d{4})(?:-(\d{2}))?(?:-(\d{2}))?$/;

// A release date for display, never more precise than what is known: a year
// is "2011", never "1 January 2011". `value` is the stored "YYYY-MM-DD" (the
// start of the period) or a partial "YYYY" / "YYYY-MM".
export function formatPartialDate(
  value: string | null | undefined,
  precision: DatePrecision | null | undefined,
  prefs: DateFormatPreferences,
): string {
  if (!value) return "";
  const match = PARTIAL.exec(value);
  if (!match) return formatDisplayDate(value);
  const known: DatePrecision =
    precision === "year" || !match[2]
      ? "year"
      : precision === "month" || !match[3]
        ? "month"
        : "day";
  if (prefs.date_display === "iso")
    return value.slice(0, { year: 4, month: 7, day: 10 }[known]);
  if (known === "year") return match[1];
  if (known === "month")
    return new Date(
      Number(match[1]),
      Number(match[2]) - 1,
      1,
    ).toLocaleDateString(undefined, {
      year: "numeric",
      month: prefs.partial_date_style === "short" ? "short" : "long",
    });
  return formatDisplayDate(value);
}

const PRECISION_RANK: Record<DatePrecision, number> = {
  year: 0,
  month: 1,
  day: 2,
};

// Orders release dates oldest first by the start of their period, and a less
// precise date first on the same start: "2011" before "April 2011" before
// "1 April 2011" (C5.5). A missing precision is a day.
export function compareReleaseDates(
  a: { date: string; precision?: DatePrecision | null },
  b: { date: string; precision?: DatePrecision | null },
): number {
  return (
    a.date.slice(0, 10).localeCompare(b.date.slice(0, 10)) ||
    PRECISION_RANK[a.precision ?? "day"] - PRECISION_RANK[b.precision ?? "day"]
  );
}
