// How closely a title matches what the user typed, for the "Best match" sort.
// Mirrors the server's ordering for media libraries so every library agrees:
// 0 exact, 1 starts with it, 2 a word starts with it, 3 contains it,
// 4 anything else the search still let through (typos, other spellings).
export type TitleMatchRank = 0 | 1 | 2 | 3 | 4;

// Shared by fuzzy title filtering and zero-result spelling suggestions.
export function levenshtein(a: string, b: string): number {
  const dp: number[][] = Array.from({ length: a.length + 1 }, (_, i) => [
    i,
    ...Array(b.length).fill(0),
  ]);
  for (let j = 0; j <= b.length; j++) dp[0][j] = j;
  for (let i = 1; i <= a.length; i++) {
    for (let j = 1; j <= b.length; j++) {
      dp[i][j] =
        a[i - 1] === b[j - 1]
          ? dp[i - 1][j - 1]
          : 1 + Math.min(dp[i - 1][j - 1], dp[i - 1][j], dp[i][j - 1]);
    }
  }
  return dp[a.length][b.length];
}

export function fuzzyTitleMatch(title: string, query: string): boolean {
  const lowerTitle = title.toLowerCase();
  if (lowerTitle.includes(query)) return true;
  if (query.length < 4) return false;
  const threshold = Math.max(1, Math.floor(query.length * 0.34));
  return lowerTitle
    .split(/\s+/)
    .some((word) => levenshtein(query, word) <= threshold);
}

export function titleMatchRank(
  titles: readonly string[],
  query: string,
): TitleMatchRank {
  const needle = query.trim().toLowerCase();
  let best: TitleMatchRank = 4;
  if (!needle) return best;
  for (const title of titles) {
    const lower = title.toLowerCase();
    let rank: TitleMatchRank = 4;
    if (lower === needle) rank = 0;
    else if (lower.startsWith(needle)) rank = 1;
    else if (lower.includes(` ${needle}`)) rank = 2;
    else if (lower.includes(needle)) rank = 3;
    if (rank < best) best = rank;
  }
  return best;
}
