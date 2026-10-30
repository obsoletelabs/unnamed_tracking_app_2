// Bulk library workflows retain a large page size; the visible Games page
// uses a separate RTT/throughput-adaptive page size and requests more only as
// the virtualized viewport approaches the loaded edge.
export const GAMES_PAGE_SIZE = 1000;
const GAMES_PAGE_CONCURRENCY = 4;
const MIN_VIEWPORT_PAGE_SIZE = 25;
// A Games page request is for a viewport window, not a bulk export. Keep its
// adaptive ceiling well below the 1,000-row bulk endpoint so a fast LAN does
// not turn the first scroll into a full-library transfer.
const MAX_VIEWPORT_PAGE_SIZE = 250;
// Desktop browsers often omit Network Information API entirely. Starting at
// 50 games left fewer than the viewport plus virtualizer overscan on common
// desktop sizes, so the near-end observer immediately started more page
// requests. Those serial RTTs showed up as a visible stall before scrolling
// had even begun. The initial window is now sized from the screen and card
// density, with less overscan on known slow links; later windows still adapt
// to measured request time.
let adaptiveViewportPageSize = 0;
let lastViewportRequestMs = 0;
let hasViewportMeasurement = false;

function estimatedViewportPageSize(bufferRows: number): number {
  if (typeof window === "undefined") return 100;
  const density = (() => {
    try {
      return localStorage.getItem("gameLibraryDensity") ?? "cozy";
    } catch {
      return "cozy";
    }
  })();
  const minCardWidth =
    density === "compact" ? 150 : density === "large" ? 260 : 200;
  // Reserve space for the fixed navigation rail and page gutters. The grid
  // uses 14px gaps and approximately 330px rows, matching GameLibrary's
  // virtualizer estimate. Request enough rows for the actual viewport and
  // its two-sided render buffer so the first scroll does not need a second
  // page RTT before filling the visible area.
  const columns = Math.max(
    1,
    Math.floor((window.innerWidth - 390 + 14) / (minCardWidth + 14)),
  );
  const visibleRows = Math.ceil(Math.max(320, window.innerHeight - 300) / 330);
  return Math.max(
    MIN_VIEWPORT_PAGE_SIZE,
    Math.min(MAX_VIEWPORT_PAGE_SIZE, columns * (visibleRows + bufferRows * 2)),
  );
}

function connectionPageSize(): number {
  if (typeof navigator === "undefined")
    return adaptiveViewportPageSize || estimatedViewportPageSize(6);
  const connection = (
    navigator as Navigator & {
      connection?: { rtt?: number; downlink?: number; effectiveType?: string };
    }
  ).connection;
  if (hasViewportMeasurement) return adaptiveViewportPageSize;
  if (!connection)
    return adaptiveViewportPageSize || estimatedViewportPageSize(6);
  if (
    connection.effectiveType === "slow-2g" ||
    connection.effectiveType === "2g" ||
    (connection.rtt ?? 0) > 400 ||
    (connection.downlink ?? 99) < 1
  )
    return estimatedViewportPageSize(1);
  if ((connection.rtt ?? 0) > 180 || (connection.downlink ?? 99) < 2)
    return estimatedViewportPageSize(2);
  if ((connection.rtt ?? 0) > 80 || (connection.downlink ?? 99) < 8)
    return estimatedViewportPageSize(4);
  return estimatedViewportPageSize(6);
}

export function viewportGamePageSize(): number {
  return Math.max(
    MIN_VIEWPORT_PAGE_SIZE,
    Math.min(MAX_VIEWPORT_PAGE_SIZE, Math.round(connectionPageSize())),
  );
}

// Buffer enough rows to cover the last measured page latency during a quick
// scroll. Network Information API RTT often ignores DevTools throttling and
// can be missing on desktop browsers, so use the observed request time first.
export function viewportPrefetchRows(): number {
  if (hasViewportMeasurement) {
    if (lastViewportRequestMs > 1_200) return 10;
    if (lastViewportRequestMs > 600) return 7;
    if (lastViewportRequestMs > 250) return 5;
    return 3;
  }
  if (typeof navigator === "undefined") return 3;
  const rtt =
    (navigator as Navigator & { connection?: { rtt?: number } }).connection
      ?.rtt ?? 0;
  if (rtt > 800) return 10;
  if (rtt > 400) return 7;
  if (rtt > 180) return 5;
  return 3;
}

async function fetchPage<T>(
  skip: number,
  limit = GAMES_PAGE_SIZE,
  signal?: AbortSignal,
  includeCounts = skip === 0,
  search?: string,
  status?: string,
  favorite?: boolean,
): Promise<{
  page: T[];
  total: number | null;
  statusCounts: Record<string, number> | null;
}> {
  const started = performance.now();
  const params = new URLSearchParams({
    skip: String(skip),
    limit: String(limit),
    include_counts: String(includeCounts),
  });
  if (search) params.set("search", search);
  if (status && status !== "all")
    params.set("status", status.toUpperCase().replace(/ /g, "_"));
  if (favorite) params.set("favorite", "true");
  const response = await fetch(`/api/game/list?${params.toString()}`, {
    credentials: "include",
    signal,
    priority: skip === 0 && limit !== GAMES_PAGE_SIZE ? "high" : "auto",
  });
  if (!response.ok) {
    throw new Error(
      `Failed to fetch games: ${response.status} ${response.statusText}`,
    );
  }
  const header = response.headers?.get("X-Total-Count") ?? null;
  const rawStatusCounts = response.headers?.get("X-Status-Counts") ?? null;
  const statusCounts = rawStatusCounts
    ? Object.fromEntries(
        rawStatusCounts
          .split(",")
          .map((entry) => entry.split(":"))
          .filter(([key, value]) => key && Number.isFinite(Number(value)))
          .map(([key, value]) => [key, Number(value)]),
      )
    : null;
  const total = header === null ? NaN : Number(header);
  const page = (await response.json()) as T[];
  if (limit !== GAMES_PAGE_SIZE) {
    lastViewportRequestMs = performance.now() - started;
    hasViewportMeasurement = true;
    if (lastViewportRequestMs > 900)
      adaptiveViewportPageSize = Math.max(
        estimatedViewportPageSize(0),
        Math.floor(limit * 0.65),
      );
    else if (lastViewportRequestMs < 180)
      adaptiveViewportPageSize = Math.min(
        MAX_VIEWPORT_PAGE_SIZE,
        Math.max(limit, Math.ceil(limit * 1.5)),
      );
    else adaptiveViewportPageSize = limit;
  }
  return {
    page,
    total: Number.isInteger(total) && total >= 0 ? total : null,
    statusCounts,
  };
}

export async function fetchViewportGamePage<T>(
  skip: number,
  signal?: AbortSignal,
  search?: string,
  status?: string,
  favorite?: boolean,
): Promise<{
  games: T[];
  total: number | null;
  nextPageSize: number;
  statusCounts: Record<string, number> | null;
}> {
  const size = viewportGamePageSize();
  const result = await fetchPage<T>(
    skip,
    size,
    signal,
    skip === 0,
    search,
    status,
    favorite,
  );
  return {
    games: result.page,
    total: result.total,
    nextPageSize: size,
    statusCounts: result.statusCounts,
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
