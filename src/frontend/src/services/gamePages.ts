// Reads the whole game list as pages of GAMES_PAGE_SIZE. The first page
// reports how many games match (X-Total-Count), so the rest are requested a
// few at a time instead of one after another.
export const GAMES_PAGE_SIZE = 200;
const GAMES_PAGE_CONCURRENCY = 4;

async function fetchPage<T>(
  skip: number,
): Promise<{ page: T[]; total: number | null }> {
  const response = await fetch(
    `/api/game/list?skip=${skip}&limit=${GAMES_PAGE_SIZE}`,
    { credentials: "include" },
  );
  if (!response.ok) {
    throw new Error(
      `Failed to fetch games: ${response.status} ${response.statusText}`,
    );
  }
  const header = response.headers?.get("X-Total-Count") ?? null;
  const total = header === null ? NaN : Number(header);
  return {
    page: (await response.json()) as T[],
    total: Number.isInteger(total) && total >= 0 ? total : null,
  };
}

export async function fetchAllGamePages<T>(): Promise<T[]> {
  const first = await fetchPage<T>(0);
  const all = [...first.page];
  if (first.page.length < GAMES_PAGE_SIZE) return all;
  if (first.total === null) {
    // A server without the total: keep reading until a short page.
    for (let skip = GAMES_PAGE_SIZE; ; skip += GAMES_PAGE_SIZE) {
      const { page } = await fetchPage<T>(skip);
      all.push(...page);
      if (page.length < GAMES_PAGE_SIZE) break;
    }
    return all;
  }
  const offsets: number[] = [];
  for (let skip = GAMES_PAGE_SIZE; skip < first.total; skip += GAMES_PAGE_SIZE)
    offsets.push(skip);
  for (let i = 0; i < offsets.length; i += GAMES_PAGE_CONCURRENCY) {
    const pages = await Promise.all(
      offsets
        .slice(i, i + GAMES_PAGE_CONCURRENCY)
        .map((skip) => fetchPage<T>(skip)),
    );
    for (const { page } of pages) all.push(...page);
  }
  return all;
}
