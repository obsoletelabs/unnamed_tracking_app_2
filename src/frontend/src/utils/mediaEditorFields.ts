import type { MovieStatus } from "../types/movie";
import { splitList } from "./formLists";
import { numberOrNull } from "./numberInput";

// The fields Movie, TV and Anime share. The three editors keep their own
// media-specific fields and reuse these conversions so a value is read,
// validated and written the same way everywhere. Everything is saved through
// each media type's existing create/update request; nothing here talks to the
// API.

// Movies, shows and anime all use the same eight real statuses.
export type MediaStatus = MovieStatus;

// What a loaded Movie, TVShow or Anime provides for these fields.
export interface SharedMedia {
  status: MediaStatus;
  favorite: boolean;
  rewatches: number;
  startDate: string | null;
  endDate: string | null;
  note: string | null;
  ratingOverall: number | null;
  ratingStory: number | null;
  ratingPerformance: number | null;
  ratingSoundtrack: number | null;
  personalRank: number | null;
  source: string | null;
  tags: string[];
  countries: string[];
  languages: string[];
  ageRating: string | null;
  features: string[];
  genres: string[];
  providerIds?: Record<string, string>;
  posterUrl: string | null;
  backdropUrl: string | null;
}

// Your own tracking values, kept apart from what a provider supplies.
export interface TrackingFields {
  status: MediaStatus;
  favorite: boolean;
  rewatches: number | null;
  startDate: string;
  endDate: string;
  note: string;
  ratingOverall: number | null;
  ratingStory: number | null;
  ratingPerformance: number | null;
  ratingSoundtrack: number | null;
  personalRank: number | null;
  source: string;
  tagsInput: string;
}

// Descriptive metadata a provider can supply and a lookup can offer.
export interface ClassificationFields {
  genresInput: string;
  countriesInput: string;
  languagesInput: string;
  ageRating: string;
  featuresInput: string;
}

export function blankTracking(): TrackingFields {
  return {
    status: "wishlist",
    favorite: false,
    rewatches: 0,
    startDate: "",
    endDate: "",
    note: "",
    ratingOverall: null,
    ratingStory: null,
    ratingPerformance: null,
    ratingSoundtrack: null,
    personalRank: null,
    source: "",
    tagsInput: "",
  };
}

export function trackingFrom(media: SharedMedia): TrackingFields {
  return {
    status: media.status,
    favorite: media.favorite,
    rewatches: media.rewatches,
    startDate: media.startDate ?? "",
    endDate: media.endDate ?? "",
    note: media.note ?? "",
    ratingOverall: media.ratingOverall,
    ratingStory: media.ratingStory,
    ratingPerformance: media.ratingPerformance,
    ratingSoundtrack: media.ratingSoundtrack,
    personalRank: media.personalRank,
    source: media.source ?? "",
    tagsInput: media.tags.join(", "),
  };
}

export function blankClassification(): ClassificationFields {
  return {
    genresInput: "",
    countriesInput: "",
    languagesInput: "",
    ageRating: "",
    featuresInput: "",
  };
}

export function classificationFrom(media: SharedMedia): ClassificationFields {
  return {
    genresInput: media.genres.join(", "),
    countriesInput: media.countries.join(", "),
    languagesInput: media.languages.join(", "),
    ageRating: media.ageRating ?? "",
    featuresInput: media.features.join(", "),
  };
}

const RATING = { min: 0, max: 10 };

// Validated request values. Throws the field's message for a bad number or a
// finish date before the start date; the caller shows it on the form.
export function trackingToInput(fields: TrackingFields) {
  if (fields.startDate && fields.endDate && fields.endDate < fields.startDate)
    throw new Error("Finished: choose a date on or after the start date.");
  const source = fields.source.trim();
  if (source.length > 50)
    throw new Error("Source: use 50 characters or fewer.");
  return {
    status: fields.status,
    favorite: fields.favorite,
    rewatches:
      numberOrNull(fields.rewatches, "Rewatches", {
        min: 0,
        integer: true,
      }) ?? 0,
    startDate: fields.startDate || null,
    endDate: fields.endDate || null,
    note: fields.note.trim() || null,
    ratingOverall: numberOrNull(fields.ratingOverall, "Your rating", RATING),
    ratingStory: numberOrNull(fields.ratingStory, "Story rating", RATING),
    ratingPerformance: numberOrNull(
      fields.ratingPerformance,
      "Performance rating",
      RATING,
    ),
    ratingSoundtrack: numberOrNull(
      fields.ratingSoundtrack,
      "Soundtrack rating",
      RATING,
    ),
    personalRank: numberOrNull(fields.personalRank, "Personal rank"),
    source: source || null,
    tags: splitList(fields.tagsInput),
  };
}

export function classificationToInput(fields: ClassificationFields) {
  const ageRating = fields.ageRating.trim();
  if (ageRating.length > 20)
    throw new Error("Age rating: use 20 characters or fewer.");
  return {
    genres: splitList(fields.genresInput),
    countries: splitList(fields.countriesInput),
    languages: splitList(fields.languagesInput),
    ageRating: ageRating || null,
    features: splitList(fields.featuresInput),
  };
}

// The request limits for provider identities (see ProviderIDs in the API).
const PROVIDER_KEY = /^[a-z0-9._-]{1,50}$/;
const PROVIDER_VALUE_MAX = 256;
const PROVIDER_COUNT_MAX = 32;

export interface ProviderIdRow {
  key: string;
  value: string;
}

export function providerIdRows(ids: Record<string, string>): ProviderIdRow[] {
  return Object.entries(ids).map(([key, value]) => ({ key, value }));
}

// Blank rows are ignored. Returns the identities and, when a row cannot be
// saved, the first problem to show next to the list.
export function providerIdsFromRows(rows: ProviderIdRow[]): {
  ids: Record<string, string>;
  error: string | null;
} {
  const ids: Record<string, string> = {};
  let error: string | null = null;
  const fail = (message: string) => (error ??= message);
  for (const row of rows) {
    const key = row.key.trim().toLowerCase();
    const value = row.value.trim();
    if (!key && !value) continue;
    if (!PROVIDER_KEY.test(key))
      fail(
        key
          ? `"${key}" is not a valid provider name; use lowercase letters, digits, dots, dashes or underscores.`
          : "Every provider ID needs a provider name.",
      );
    else if (!value) fail(`Enter the ID for ${key} or remove the row.`);
    else if (value.length > PROVIDER_VALUE_MAX)
      fail(`The ${key} ID is longer than ${PROVIDER_VALUE_MAX} characters.`);
    else if (key in ids) fail(`${key} is listed more than once.`);
    ids[key] = value;
  }
  if (Object.keys(ids).length > PROVIDER_COUNT_MAX)
    fail(`Use at most ${PROVIDER_COUNT_MAX} provider IDs.`);
  return { ids, error };
}

// TV and Anime also keep their primary provider ID in a legacy column that a
// lookup fills together with the identity map. Follow an edited identity, and
// clear the column only when it was a copy of the identity that was removed.
export function followProviderId(
  legacy: string | null,
  original: string | undefined,
  edited: string | undefined,
): string | null {
  if (edited) return edited;
  return legacy !== null && legacy === original ? null : legacy;
}

// The tabs all three editors share, in order.
export const MEDIA_EDITOR_TABS = [
  "Basics",
  "Details",
  "Tracking",
  "Artwork",
  "Links",
] as const;
export type MediaEditorTab = (typeof MEDIA_EDITOR_TABS)[number];

// Names offered while typing a provider identity.
export const MOVIE_PROVIDER_KEYS = ["tmdb", "imdb", "omdb"] as const;
export const TV_PROVIDER_KEYS = ["tmdb", "imdb", "tvmaze", "tvdb"] as const;
export const ANIME_PROVIDER_KEYS = [
  "anilist",
  "mal",
  "kitsu",
  "tmdb",
  "imdb",
] as const;
