// Small helpers shared by the Achievements tab and an achievement's own page.
import type { Achievement, AchievementKind } from "../types/game";

// PlayStation and RetroAchievements unlocks have no stored time, so "unlocked"
// can't be read from the time alone: use the flag the API sends when there is
// one, and fall back to the time only for data that predates it.
export function isUnlocked(a: Achievement): boolean {
  return a.unlocked ?? a.unlockedAt !== null;
}

function pad(value: number): string {
  return String(value).padStart(2, "0");
}

// The value a datetime-local input expects, in the viewer's own time zone.
export function toLocalInput(value: Date): string {
  return (
    `${value.getFullYear()}-${pad(value.getMonth() + 1)}-${pad(value.getDate())}` +
    `T${pad(value.getHours())}:${pad(value.getMinutes())}`
  );
}

// Unix seconds for a datetime-local value, or null when it isn't a real time.
export function localInputToUnixSeconds(value: string): number | null {
  if (!value) return null;
  const time = new Date(value).getTime();
  return Number.isFinite(time) ? Math.floor(time / 1000) : null;
}

// the icon to show: a provider's greyed-out one while it is still locked
export function iconOf(a: Achievement): string | null {
  if (!isUnlocked(a) && a.lockedIconUrl) return a.lockedIconUrl;
  return a.iconUrl ?? null;
}

export const KIND_LABEL: Record<AchievementKind, string> = {
  progression: "Progression",
  missable: "Missable",
  win_condition: "Win condition",
};

// providers spell the type differently ("win_condition", "Win Condition")
export function normalizeKind(
  raw: string | null | undefined,
): AchievementKind | null {
  const key = (raw ?? "")
    .trim()
    .toLowerCase()
    .replace(/[\s-]+/g, "_");
  return key in KIND_LABEL ? (key as AchievementKind) : null;
}

// the share of all players who have it, one decimal like Steam shows it
export function formatPercent(percent: number): string {
  if (percent > 0 && percent < 0.1) return "<0.1%";
  return `${percent.toFixed(1)}%`;
}

export function unlockedOn(iso: string): { date: string; time: string } {
  const d = new Date(iso);
  return {
    date: d.toLocaleDateString(),
    time: d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }),
  };
}
