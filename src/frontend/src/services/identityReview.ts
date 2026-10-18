import { failedRequest } from "./apiError";

export interface ReviewGame {
  id: string;
  title: string;
  source: string | null;
  platform: string | null;
  release_date: string | null;
  external_id: string | null;
  provider_ids: Record<string, string>;
  status: string;
  playtime_seconds: number;
  last_played_at: number | null;
}

export interface ReviewDifference {
  field: string;
  first: unknown;
  second: unknown;
  same: boolean;
}

export interface ReviewEntry {
  id: string;
  /** "copy": another store, platform or edition; "duplicate": the same copy twice. */
  kind?: "copy" | "duplicate";
  confidence: number;
  reason: string | null;
  evidence: { matched?: string[]; conflicts?: string[]; missing?: string[] };
  first: ReviewGame;
  second: ReviewGame;
  differences: ReviewDifference[];
}

export interface AutomaticMerge {
  id: string;
  confidence: number;
  reason: string | null;
  absorbed: ReviewGame;
  into: ReviewGame;
}

const BASE = "/api/game-identity/review";

async function send(path: string, init: RequestInit = {}): Promise<Response> {
  const response = await fetch(`${BASE}${path}`, {
    credentials: "include",
    ...init,
  });
  if (!response.ok) throw await failedRequest(response);
  return response;
}

export async function runIdentityMatching(): Promise<void> {
  await send("/run", { method: "POST" });
}

export async function fetchReviewQueue(
  signal?: AbortSignal,
): Promise<{ entries: ReviewEntry[]; has_more: boolean }> {
  return (await send("/queue", { signal })).json();
}

export async function fetchAutomaticMerges(
  signal?: AbortSignal,
): Promise<AutomaticMerge[]> {
  return (await send("/automatic", { signal })).json().then((r) => r.merges);
}

/** Accept a suggestion. `mainId` picks the main copy; omitted, the furthest
 * progressed and most played copy stays main. */
export async function mergeReviewEntry(
  id: string,
  mainId?: string,
): Promise<void> {
  await send(`/queue/${id}/merge`, {
    method: "POST",
    ...(mainId
      ? {
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ target_id: mainId }),
        }
      : {}),
  });
}

export async function keepReviewEntrySeparate(id: string): Promise<void> {
  await send(`/queue/${id}/keep-separate`, { method: "POST" });
}

export async function undoAutomaticMerge(id: string): Promise<void> {
  await send(`/automatic/${id}/undo`, { method: "POST" });
}

export async function keepAutomaticMerge(id: string): Promise<void> {
  await send(`/automatic/${id}/keep`, { method: "POST" });
}
