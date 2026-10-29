import { copyName, storeName } from "./gameCopies";

/** A connection currently reporting a game: Playnite, a plugin, a Steam sync... */
export interface Reporter {
  kind: "host" | "plugin" | "manual" | "metadata" | string;
  source: string;
  label: string;
  disconnected: boolean;
}

export interface OriginGame {
  source: string | null;
  platform: string | null;
  reported_by?: Reporter[];
}

/**
 * How a record is described to the person deciding about it. The store says
 * what the record claims to be ("Epic"); who reported it says how it reached
 * the library ("Playnite", "Epic Games plugin"). Playnite names the store of
 * every game it finds, so without this a Playnite record of an Epic game and
 * the Epic plugin's record look identical.
 */
export function reporterName(reporter: Reporter): string {
  if (reporter.kind === "manual") return "Added manually";
  if (reporter.kind === "plugin") return `${reporter.label} plugin`;
  if (reporter.kind === "metadata") return `${reporter.label} metadata`;
  if (reporter.source === "playnite") return "Playnite";
  const store = storeName(reporter.source) ?? reporter.label;
  return `${store} sync`;
}

/** Distinct reporter names, Playnite first because it is the one people ask about. */
export function reporterNames(game: OriginGame): string[] {
  const names = (game.reported_by ?? []).map(reporterName);
  const unique = [...new Set(names)];
  return unique.sort(
    (a, b) => Number(b === "Playnite") - Number(a === "Playnite"),
  );
}

export function cameFromPlaynite(game: OriginGame): boolean {
  return (game.reported_by ?? []).some(
    (reporter) => reporter.kind === "host" && reporter.source === "playnite",
  );
}

/** "Epic · PC (Windows), from Playnite" for text-only places. */
export function originSentence(game: OriginGame): string {
  const copy = copyName(game);
  const names = reporterNames(game).filter((name) => name !== "Added manually");
  return names.length ? `${copy}, from ${names.join(" and ")}` : copy;
}

/**
 * Whether two records can be one copy reported by two sources. Playnite names
 * the store of every game, so "Epic" and "Epic Games" are one store, but a
 * Steam record and an Epic record are different copies of the game. The server
 * enforces the same rule.
 */
export function sameCopyBlocker(
  first: OriginGame,
  second: OriginGame,
): string | null {
  const a = storeName(first.source);
  const b = storeName(second.source);
  if (a && b && a !== b)
    return `${a} and ${b} are different stores, so these are different copies.`;
  const x = first.platform?.trim().toLowerCase();
  const y = second.platform?.trim().toLowerCase();
  if (x && y && x !== y)
    return "They are on different platforms, so these are different copies.";
  return null;
}
