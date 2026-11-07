// A game's release date as precisely as it is known (CONTRACTS-v1 C5.5), in
// the signed-in person's date style, and the library's release-date order.
import { preferences } from "../state/preferences";
import type { Game, GameRelease } from "../types/game";
import {
  compareReleaseDates,
  formatPartialDate,
  type DateFormatPreferences,
} from "./dates";

type Released = Pick<Game, "releaseDate" | "releaseDatePrecision">;

export function formatReleaseDate(game: Released): string {
  return formatPartialDate(
    game.releaseDate,
    game.releaseDatePrecision,
    preferences.value,
  );
}

// The game page's "Released" fact, as precisely as the date is known ("2011",
// "April 2011" or the full day, C5.5), with the field its source label uses.
export function releaseFacts(
  game: Released,
): { label: string; value: string; field: string }[] {
  if (!game.releaseDate) return [];
  return [
    {
      label: "Released",
      value: formatReleaseDate(game),
      field: "release_date",
    },
  ];
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

const REGION_NAMES: Record<string, string> = {
  ww: "Worldwide",
  eu: "Europe",
  na: "North America",
  asia: "Asia",
  oce: "Oceania",
  latam: "Latin America",
};
const CATEGORY_NAMES: Record<string, string> = {
  early_access: "Early access",
  beta: "Beta",
  rerelease: "Re-release",
  port: "Port",
  demo: "Demo",
};

// A region code from the contract (C5): the named groups above, otherwise an
// ISO 3166-1 country, named in the viewer's language where the browser can.
export function regionName(code: string | null | undefined): string | null {
  if (!code) return null;
  const key = code.toLowerCase();
  if (REGION_NAMES[key]) return REGION_NAMES[key];
  try {
    const names = new Intl.DisplayNames(undefined, { type: "region" });
    return names.of(key.toUpperCase()) ?? key.toUpperCase();
  } catch {
    return key.toUpperCase();
  }
}

export interface ReleaseRow {
  key: string;
  date: string;
  where: string;
  edition: string | null;
  // null for an ordinary full release
  category: string | null;
}

// The game's regional and edition releases, oldest first, each date no more
// precise than known (C5.5): "March 2015 · PC · Worldwide".
export function releaseRows(
  releases: readonly GameRelease[] | undefined,
  prefs: DateFormatPreferences = preferences.value,
): ReleaseRow[] {
  return [...(releases ?? [])]
    .filter((release) => !!release.date)
    .sort(compareReleaseDates)
    .map((release) => ({
      key: [
        release.platform,
        release.region,
        release.edition,
        release.category,
        release.date,
      ].join("|"),
      date: formatPartialDate(release.date, release.precision, prefs),
      where: [
        release.platform
          ? release.platform.length <= 4
            ? release.platform.toUpperCase()
            : release.platform[0].toUpperCase() + release.platform.slice(1)
          : null,
        regionName(release.region),
      ]
        .filter(Boolean)
        .join(" · "),
      edition: release.edition,
      category: release.category
        ? (CATEGORY_NAMES[release.category] ?? null)
        : null,
    }));
}
