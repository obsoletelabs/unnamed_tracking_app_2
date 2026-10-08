import type { LocationQuery, LocationQueryRaw } from "vue-router";
import type { GameStatus } from "../types/game";
import { gameGenres } from "./genres";

export const GAME_SORT_KEYS = [
  "name",
  "name_desc",
  "recent",
  "rating",
  "playtime",
  "last_played",
  "neglected",
  "priority",
  "release",
  "length",
] as const;
export const GAME_STATUS_OPTIONS: (GameStatus | "all")[] = [
  "all",
  "playing",
  "beaten",
  "mastered",
  "played",
  "on hold",
  "dropped",
  "backlog",
  "wishlist",
];

export interface GameLibraryFilters {
  searchQuery: string;
  statusFilter: GameStatus | "all";
  platformFilter: string;
  sortBy: (typeof GAME_SORT_KEYS)[number];
  franchiseFilter: string;
  collectionFilter: string;
  companyFilter: string;
  ageRatingFilter: string;
  regionFilter: string;
  languageFilter: string;
  metadataProviderFilter: string;
  favoritesOnly: boolean;
  achievementsFilter: "all" | "has" | "none";
  retroAchievementsOnly: boolean;
  missingFilter: "none" | "playtime" | "rating" | "tags" | "description";
  tagsFilter: string[];
}

export const DEFAULT_GAME_FILTERS: GameLibraryFilters = {
  searchQuery: "",
  statusFilter: "all",
  platformFilter: "all",
  sortBy: "name",
  franchiseFilter: "all",
  collectionFilter: "all",
  companyFilter: "all",
  ageRatingFilter: "all",
  regionFilter: "all",
  languageFilter: "all",
  metadataProviderFilter: "all",
  favoritesOnly: false,
  achievementsFilter: "all",
  retroAchievementsOnly: false,
  missingFilter: "none",
  tagsFilter: [],
};

const TEXT_FILTERS = {
  platform: "platformFilter",
  series: "franchiseFilter",
  collection: "collectionFilter",
  company: "companyFilter",
  age: "ageRatingFilter",
  region: "regionFilter",
  language: "languageFilter",
  provider: "metadataProviderFilter",
} as const;
const QUERY_KEYS = [
  "filters",
  "q",
  "status",
  "sort",
  "tag",
  "genre",
  "favorites",
  "achievements",
  "retro",
  "missing",
  ...Object.keys(TEXT_FILTERS),
];

// Old presets and provider labels may contain both "Genre: Indie" and "Indie".
// Use the same spelling as the picker, without expanding a selection into aliases.
export function normalizeGameTags(tags: string[], genre = "all"): string[] {
  const values = genre && genre !== "all" ? [...tags, genre] : tags;
  const normalized = values
    .map((tag) => gameGenres([tag])[0])
    .filter((tag): tag is string => !!tag);
  return [
    ...new Map(normalized.map((tag) => [tag.toLowerCase(), tag])).values(),
  ];
}

export function hasGameLibraryQuery(query: LocationQuery): boolean {
  return QUERY_KEYS.some((key) => key in query);
}

export function readGameLibraryQuery(query: LocationQuery): GameLibraryFilters {
  const filters: GameLibraryFilters = {
    ...DEFAULT_GAME_FILTERS,
    tagsFilter: [],
  };
  const values = (key: string): string[] => {
    const value = query[key];
    return (Array.isArray(value) ? value : [value]).filter(
      (v): v is string => typeof v === "string" && !!v.trim(),
    );
  };
  const pick = (key: string) => values(key)[0];
  filters.searchQuery = pick("q") ?? "";
  for (const [key, field] of Object.entries(TEXT_FILTERS))
    filters[field] = pick(key) ?? "all";
  const status = pick("status");
  if (GAME_STATUS_OPTIONS.includes(status as GameStatus))
    filters.statusFilter = status as GameStatus;
  const sort = pick("sort");
  if (GAME_SORT_KEYS.includes(sort as GameLibraryFilters["sortBy"]))
    filters.sortBy = sort as GameLibraryFilters["sortBy"];
  const achievements = pick("achievements");
  if (achievements === "has" || achievements === "none")
    filters.achievementsFilter = achievements;
  const missing = pick("missing");
  if (
    missing === "playtime" ||
    missing === "rating" ||
    missing === "tags" ||
    missing === "description"
  )
    filters.missingFilter = missing;
  filters.favoritesOnly = pick("favorites") === "1";
  filters.retroAchievementsOnly = pick("retro") === "1";
  filters.tagsFilter = normalizeGameTags([
    ...values("tag"),
    ...values("genre"),
  ]);
  return filters;
}

export function writeGameLibraryQuery(
  filters: GameLibraryFilters,
  query: LocationQuery = {},
): LocationQueryRaw {
  const result: LocationQueryRaw = { ...query };
  for (const key of QUERY_KEYS) delete result[key];
  // An explicit empty filter set must override the recipient's saved filters too.
  result.filters = "1";
  if (filters.searchQuery) result.q = filters.searchQuery;
  if (filters.statusFilter !== "all") result.status = filters.statusFilter;
  if (filters.sortBy !== "name") result.sort = filters.sortBy;
  for (const [key, field] of Object.entries(TEXT_FILTERS))
    if (filters[field] !== "all") result[key] = filters[field];
  const tags = normalizeGameTags(filters.tagsFilter);
  if (tags.length) result.tag = tags;
  if (filters.favoritesOnly) result.favorites = "1";
  if (filters.retroAchievementsOnly) result.retro = "1";
  if (filters.achievementsFilter !== "all")
    result.achievements = filters.achievementsFilter;
  if (filters.missingFilter !== "none") result.missing = filters.missingFilter;
  return result;
}
