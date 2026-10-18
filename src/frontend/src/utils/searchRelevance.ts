// How closely a title matches what the user typed, for the "Best match" sort.
// Mirrors the server's ordering for media libraries so every library agrees:
// 0 exact, 1 starts with it, 2 a word starts with it, 3 contains it,
// 4 anything else the search still let through (typos, other spellings).
export type TitleMatchRank = 0 | 1 | 2 | 3 | 4;

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
