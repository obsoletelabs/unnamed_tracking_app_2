import { failedRequest } from "./apiError";
import type { Reporter } from "../utils/gameOrigin";

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
  /** The connections reporting this record: Playnite, a plugin, a store sync. */
  reported_by?: Reporter[];
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

/** How the other copy shows in the library once grouped. */
export type CopyDisplay = "default" | "link" | "hidden";

/** Accept a suggestion. `mainId` picks the main copy; omitted, the furthest
 * progressed and most played copy stays main. `display` is how the other copy
 * shows in the library (default: folded under the main entry). `sameCopy`
 * says the two records are one copy reported by two sources, so its playtime
 * is counted once; the server refuses it for different stores or platforms. */
export async function mergeReviewEntry(
  id: string,
  mainId?: string,
  display?: CopyDisplay,
  sameCopy = false,
): Promise<void> {
  const body = {
    ...(mainId ? { target_id: mainId } : {}),
    ...(display && display !== "default" ? { display } : {}),
    ...(sameCopy ? { same_copy: true } : {}),
  };
  await send(`/queue/${id}/merge`, {
    method: "POST",
    ...(Object.keys(body).length
      ? {
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        }
      : {}),
  });
}

/** Different games where one is a remake, port, DLC... of the other. Both
 * stay in the library and the pair is not suggested again. */
export async function relateReviewEntry(
  id: string,
  relation: { kind: string; fromId: string },
): Promise<void> {
  await send(`/queue/${id}/relate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ kind: relation.kind, from_id: relation.fromId }),
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
