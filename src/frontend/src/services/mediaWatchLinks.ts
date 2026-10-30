import type { MediaType } from "./mediaExtras";

// Places a title can be watched, as recorded when a library source last synced it. Nothing is
// looked up live: the link and whether the item is still there were saved by the sync.
export interface WatchLink {
  id: string;
  label: string;
  url: string;
}

interface SourceRow {
  source?: unknown;
  account_scope?: unknown;
  available?: unknown;
  watch_url?: unknown;
  watch_label?: unknown;
}

function webAddress(value: unknown): string | null {
  if (typeof value !== "string") return null;
  try {
    const url = new URL(value);
    return url.protocol === "http:" || url.protocol === "https:"
      ? url.href
      : null;
  } catch {
    return null;
  }
}

function sourceName(source: string): string {
  return source ? source.charAt(0).toUpperCase() + source.slice(1) : "library";
}

/** The sources that still have the title and recorded a web address to open it at. */
export function watchLinks(sources: SourceRow[]): WatchLink[] {
  const links: WatchLink[] = [];
  for (const row of sources) {
    const url = webAddress(row.watch_url);
    if (!url || row.available === false) continue;
    const source = typeof row.source === "string" ? row.source : "";
    const label =
      typeof row.watch_label === "string" && row.watch_label.trim()
        ? row.watch_label.trim()
        : sourceName(source);
    links.push({
      id: `${source}:${typeof row.account_scope === "string" ? row.account_scope : ""}`,
      label,
      url,
    });
  }
  return links;
}

export async function fetchWatchLinks(
  mediaType: MediaType,
  mediaId: string,
  signal?: AbortSignal,
): Promise<WatchLink[]> {
  const params = new URLSearchParams({
    media_type: mediaType,
    media_id: mediaId,
    limit: "1",
  });
  const response = await fetch(`/api/media/provider-state?${params}`, {
    credentials: "include",
    signal,
  });
  if (!response.ok) return [];
  const body = (await response.json()) as { sources?: SourceRow[] };
  return watchLinks(body.sources ?? []);
}
