// A game's release date as precisely as it is known (CONTRACTS-v1 C5.5), in
// the signed-in person's date style, and the library's release-date order.
import { preferences } from "../state/preferences";
import type { Game } from "../types/game";
import { compareReleaseDates, formatPartialDate } from "./dates";

type Released = Pick<Game, "releaseDate" | "releaseDatePrecision">;

export function formatReleaseDate(game: Released): string {
  return formatPartialDate(
    game.releaseDate,
    game.releaseDatePrecision,
    preferences.value,
  );
}

// Newest first; a year-only "2011" counts from the start of its year, and a
// game without a release date sorts last.
export function newestRelease(a: Released, b: Released): number {
  if (!a.releaseDate || !b.releaseDate)
    return Number(!a.releaseDate) - Number(!b.releaseDate);
  return compareReleaseDates(
    { date: b.releaseDate, precision: b.releaseDatePrecision },
    { date: a.releaseDate, precision: a.releaseDatePrecision },
  );
}
