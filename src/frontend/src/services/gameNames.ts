import { failedRequest } from "./apiError";

export interface NameReview {
  id: string;
  game_id: string;
  current_title: string;
  proposed_title: string;
  origin: string;
  quality: string;
  observation_id: string | null;
  recorded_at: number;
}

export interface NameRepairResult {
  checked: number;
  replaced: string[];
  proposed: string[];
  unresolved: string[];
  dry_run: boolean;
}

const BASE = "/api/game-identity/names";

async function send<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(`${BASE}${path}`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body ?? {}),
  });
  if (!response.ok) throw await failedRequest(response);
  return response.json();
}

export async function fetchNameReviews(
  signal?: AbortSignal,
): Promise<NameReview[]> {
  const response = await fetch(`${BASE}/reviews`, {
    credentials: "include",
    signal,
  });
  if (!response.ok) throw await failedRequest(response);
  return response.json();
}

/** Update: take the name the source reported. */
export function updateGameName(id: string): Promise<{ title: string }> {
  return send(`/reviews/${id}/update`);
}

/** Keep current: the same source name is not offered again. */
export function keepGameName(id: string): Promise<{ title: string }> {
  return send(`/reviews/${id}/keep`);
}

export function repairGameNames(dryRun = false): Promise<NameRepairResult> {
  return send("/repair", { dry_run: dryRun });
}
