// How closely a title matches a library search, lowest first: the same
// exact > prefix > word start > substring order metadata searches rank by.
// Titles that only matched through typo tolerance rank last.
export const SEARCH_MATCH_RANK = {
  exact: 0,
  prefix: 1,
  wordStart: 2,
  substring: 3,
  fuzzy: 4,
} as const;

export function searchMatchRank(title: string, query: string): number {
  const q = query.trim().toLowerCase();
  const lowerTitle = title.trim().toLowerCase();
  if (!q) return SEARCH_MATCH_RANK.fuzzy;
  if (lowerTitle === q) return SEARCH_MATCH_RANK.exact;
  if (lowerTitle.startsWith(q)) return SEARCH_MATCH_RANK.prefix;
  if (lowerTitle.split(/[^\p{L}\p{N}]+/u).some((word) => word.startsWith(q)))
    return SEARCH_MATCH_RANK.wordStart;
  if (lowerTitle.includes(q)) return SEARCH_MATCH_RANK.substring;
  return SEARCH_MATCH_RANK.fuzzy;
}

// Best rank across every title an entry is known by (e.g. each game copy).
// Returns 0 for ties so callers can chain their usual sort with `||`.
export function compareBySearchMatch(
  aTitles: string[],
  bTitles: string[],
  query: string,
): number {
  const best = (titles: string[]) =>
    Math.min(
      SEARCH_MATCH_RANK.fuzzy,
      ...titles.map((title) => searchMatchRank(title, query)),
    );
  return best(aTitles) - best(bTitles);
}
