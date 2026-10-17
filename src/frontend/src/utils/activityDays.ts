// Pure helpers for the Statistics activity heatmap and its day dialog. Days
// are calendar dates ("2026-10-16") in the zone the server was asked about;
// all arithmetic here is UTC-based so the browser's own zone never shifts them.
import type { ActivityDayItem } from "../services/mediaStats";

const DAY_MS = 86_400_000;

export function parseDay(value: string): Date {
  const [year, month, day] = value.split("-").map(Number);
  return new Date(Date.UTC(year!, month! - 1, day!));
}

export function formatDay(date: Date): string {
  return date.toISOString().slice(0, 10);
}

export function addDays(value: string, days: number): string {
  return formatDay(new Date(parseDay(value).getTime() + days * DAY_MS));
}

export function longDay(value: string): string {
  return parseDay(value).toLocaleDateString(undefined, {
    weekday: "long",
    year: "numeric",
    month: "long",
    day: "numeric",
    timeZone: "UTC",
  });
}

export function monthName(value: string): string {
  return parseDay(value).toLocaleDateString(undefined, {
    month: "short",
    timeZone: "UTC",
  });
}

function plural(n: number, noun: string): string {
  return `${n} ${noun}${n === 1 ? "" : "s"}`;
}

// "3 episodes, 1 achievement", or "nothing" when the day has neither
export function describeCounts(day: {
  episodes?: number;
  achievements?: number;
}): string {
  const parts: string[] = [];
  if (day.episodes) parts.push(plural(day.episodes, "episode"));
  if (day.achievements) parts.push(plural(day.achievements, "achievement"));
  return parts.join(", ") || "nothing";
}

const MEDIA_ROUTE = { movie: "/movies", tv: "/tv", anime: "/anime" } as const;

// Where a day entry opens. Every id came from the caller's own, non-deleted
// rows (the server drops everything else), so a link never points elsewhere.
export function itemPath(item: ActivityDayItem): string {
  return item.kind === "achievement"
    ? `/games/${item.game_id}/achievements/${item.id}`
    : `${MEDIA_ROUTE[item.media_type]}/${item.id}`;
}

export interface MonthMark {
  week: number;
  label: string;
}

// Month labels sit over the first week column that contains the 1st of the
// month; the first column is labelled with the month it starts in.
export function monthMarks(weekStarts: string[]): MonthMark[] {
  const marks: MonthMark[] = [];
  weekStarts.forEach((start, week) => {
    for (let i = 0; i < 7; i++) {
      const day = addDays(start, i);
      if (day.endsWith("-01") || (week === 0 && i === 0))
        marks.push({ week, label: monthName(day) });
      if (day.endsWith("-01")) break;
    }
  });
  // drop a label that would sit on top of the next one
  return marks.filter(
    (m, i) => i === marks.length - 1 || marks[i + 1]!.week - m.week >= 3,
  );
}
