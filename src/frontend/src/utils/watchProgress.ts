// Where you left off in a movie (#191), stored as minutes and shown as h:mm.

// Accept minutes, h:mm or hours/minutes text; blank -> null; invalid -> NaN.
export function parseProgressMinutes(value: string): number | null {
  const text = value.trim().toLowerCase();
  if (!text) return null;
  const clock = /^(\d+):([0-5]?\d)$/.exec(text);
  const hours = /^(\d+(?:\.\d+)?)\s*h(?:ours?|rs?)?$/.exec(text);
  let minutes: number;
  if (clock) {
    minutes = Number(clock[1]) * 60 + Number(clock[2]);
  } else if (/^\d+$/.test(text)) {
    minutes = Number(text);
  } else if (hours) {
    minutes = Math.round(Number(hours[1]) * 60);
  } else {
    const words =
      /^(?:(\d+)\s*h(?:ours?|rs?)?)?\s*(?:(\d+)\s*(?:m(?:in(?:ute)?s?)?)?)?$/.exec(
        text,
      );
    if (!words || (!words[1] && !words[2])) return Number.NaN;
    const rest = Number(words[2] ?? 0);
    if (words[1] && rest > 59) return Number.NaN;
    minutes = Number(words[1] ?? 0) * 60 + rest;
  }
  // Match the API/database integer range, including unknown-runtime movies.
  return Number.isSafeInteger(minutes) && minutes <= 2147483647
    ? minutes
    : Number.NaN;
}

// 72 -> "1:12", 5 -> "0:05"
export function formatProgressMinutes(minutes: number): string {
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return `${hours}:${String(rest).padStart(2, "0")}`;
}

// 0..100, or null when the runtime isn't known
export function progressPercent(
  minutes: number | null,
  runtimeMinutes: number | null,
): number | null {
  if (minutes === null || !runtimeMinutes) return null;
  return Math.min(100, Math.round((minutes / runtimeMinutes) * 100));
}
