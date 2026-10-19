// Week layout and bounded date ranges for the calendar (#251).

export type WeekStart = 0 | 1 | 2 | 3 | 4 | 5 | 6; // 0 Sunday .. 6 Saturday

export const WEEKDAY_NAMES = [
  "Sunday",
  "Monday",
  "Tuesday",
  "Wednesday",
  "Thursday",
  "Friday",
  "Saturday",
] as const;

const DAY_MS = 86_400_000;

export function isWeekStart(value: unknown): value is WeekStart {
  return (
    Number.isInteger(value) && (value as number) >= 0 && (value as number) <= 6
  );
}

/** Short weekday headings in the order the grid shows them. */
export function weekdayLabels(weekStart: WeekStart): string[] {
  const names = WEEKDAY_NAMES.map((name) => name.slice(0, 3));
  return [...names.slice(weekStart), ...names.slice(0, weekStart)];
}

/** `YYYY-MM-DD` of a local calendar day (never shifted by the timezone). */
export function dayKey(date: Date): string {
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${date.getFullYear()}-${month}-${day}`;
}

/** Midnight at the start of the week containing `date`. */
export function startOfWeek(date: Date, weekStart: WeekStart): Date {
  const start = new Date(date.getFullYear(), date.getMonth(), date.getDate());
  start.setDate(start.getDate() - ((start.getDay() - weekStart + 7) % 7));
  return start;
}

/** The first and last day shown by a month grid, padded out to whole weeks. */
export function monthGridRange(
  year: number,
  month: number,
  weekStart: WeekStart,
): { first: Date; last: Date; cells: number } {
  const leading = (new Date(year, month, 1).getDay() - weekStart + 7) % 7;
  const daysInMonth = new Date(year, month + 1, 0).getDate();
  const cells = Math.ceil((leading + daysInMonth) / 7) * 7;
  const first = new Date(year, month, 1 - leading);
  const last = new Date(year, month, 1 - leading + cells - 1);
  return { first, last, cells };
}

/** Whole days from `from` to `to` by calendar date (DST-safe). */
export function daysBetween(from: Date, to: Date): number {
  const a = Date.UTC(from.getFullYear(), from.getMonth(), from.getDate());
  const b = Date.UTC(to.getFullYear(), to.getMonth(), to.getDate());
  return Math.round((b - a) / DAY_MS);
}

/** How far ahead the calendar can be browsed, in days (the server allows about a year). */
export const CALENDAR_AHEAD_DAYS = 396;
/** The initial request covers this many days; later months are fetched on demand. */
export const CALENDAR_INITIAL_DAYS = 90;

/** Whether a whole grid lies inside what the first request already loaded. */
export function insideInitialWindow(last: Date, today: Date): boolean {
  return daysBetween(today, last) <= CALENDAR_INITIAL_DAYS;
}

/** Whether a month can be browsed to: not beyond the calendar's ahead limit. */
export function monthReachable(
  year: number,
  month: number,
  today: Date,
): boolean {
  return daysBetween(today, new Date(year, month, 1)) <= CALENDAR_AHEAD_DAYS;
}
