import { providerRequestError } from "./apiError";

export interface LibrarySyncResult {
  games_added: number;
  games_updated: number;
  achievements_synced: number;
  // every title the sync touched, in the order it processed them, used to
  // animate a completion feed once the (single, all-at-once) request
  // resolves. Not truly live during the request itself; the backend has no
  // streaming endpoint for this yet.
  games: string[];
  // Steam only: wishlist games added, and games whose store details, tags or
  // artwork could not be filled in (they keep what they have)
  wishlist_added?: number;
  wishlist_failed?: boolean;
  wishlist_error?: string;
  // games whose details or artwork were not filled in (provider error or
  // artwork unavailable); `enrich_retry_ids` can be passed to
  // `retrySteamEnrichment`, which is safe to repeat
  enrich_failed?: number;
  enrich_retry_ids?: string[];
  achievements_failed?: number;
  achievements_unavailable?: string[];
}

interface SteamImportResult extends LibrarySyncResult {
  enrich_game_ids?: string[];
  achievement_game_ids?: string[];
  status_game_ids?: string[];
}

export type LibrarySyncProvider = "steam" | "psn" | "retroachievements";

export async function syncLibrary(
  provider: LibrarySyncProvider,
): Promise<LibrarySyncResult> {
  if (import.meta.env.VITE_USE_MOCK_DATA === "true") {
    return {
      games_added: 0,
      games_updated: 0,
      achievements_synced: 0,
      games: [],
    };
  }

  const suffix = provider === "steam" ? "?achievements=later" : "";
  let response: Response;
  try {
    response = await fetch(`/api/library-sync/${provider}${suffix}`, {
      method: "POST",
      credentials: "include",
    });
  } catch {
    // The request may have finished on the server after the connection was lost.
    throw new Error(
      "Lost the connection before the import reported back. It may have finished on the server: check your library. Running it again is safe and will not duplicate games.",
    );
  }
  if (!response.ok) throw await providerRequestError(response);
  const result: SteamImportResult = await response.json();
  if (provider === "steam") await finishSteamImport(result);
  return result;
}

// Saving the games is quick; reading each new game's store page, tags and
// artwork is not (about a second each). The server does that a few games at a
// time, so one request never runs long enough to time out.
const ENRICH_BATCH = 5;
const ACHIEVEMENT_BATCH = 5;

async function postSteamStep<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(`/api/library-sync/steam/${path}`, {
    method: "POST",
    credentials: "include",
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!response.ok) throw await providerRequestError(response);
  return (await response.json()) as T;
}

async function finishSteamImport(result: SteamImportResult): Promise<void> {
  const achievementIds = [...new Set(result.achievement_game_ids ?? [])];
  const statusIds = new Set(result.status_game_ids ?? []);
  result.achievements_synced ??= 0;
  result.achievements_failed = 0;
  result.achievements_unavailable = [];
  for (let i = 0; i < achievementIds.length; i += ACHIEVEMENT_BATCH) {
    const batchIds = achievementIds.slice(i, i + ACHIEVEMENT_BATCH);
    try {
      const batch = await postSteamStep<{
        achievements_synced: number;
        achievements_unavailable: string[];
      }>("achievements", {
        game_ids: batchIds,
        status_game_ids: batchIds.filter((id) => statusIds.has(id)),
      });
      result.achievements_synced += batch.achievements_synced;
      result.achievements_unavailable.push(...batch.achievements_unavailable);
    } catch {
      result.achievements_failed += batchIds.length;
    }
  }
  const ids = [...(result.enrich_game_ids ?? [])];
  try {
    const wishlist = await postSteamStep<{ added: number; game_ids: string[] }>(
      "wishlist",
    );
    result.wishlist_added = wishlist.added;
    ids.push(...wishlist.game_ids);
  } catch (e) {
    result.wishlist_failed = true;
    result.wishlist_error = e instanceof Error ? e.message : undefined;
  }
  const retry = await enrichSteamGames(ids);
  result.enrich_failed = retry.length;
  result.enrich_retry_ids = retry;
}

// Fills in details and artwork in bounded batches and returns the ids that
// still need another try (the request failed, or the provider or artwork was
// unavailable). Repeating it only fills what is still empty.
async function enrichSteamGames(ids: string[]): Promise<string[]> {
  const retry: string[] = [];
  for (let i = 0; i < ids.length; i += ENRICH_BATCH) {
    const batchIds = ids.slice(i, i + ENRICH_BATCH);
    try {
      const batch = await postSteamStep<{ retry_game_ids?: string[] }>(
        "enrich",
        { game_ids: batchIds },
      );
      retry.push(...(batch.retry_game_ids ?? []));
    } catch {
      retry.push(...batchIds);
    }
  }
  return retry;
}

export async function retrySteamEnrichment(ids: string[]): Promise<string[]> {
  if (import.meta.env.VITE_USE_MOCK_DATA === "true") return [];
  return enrichSteamGames([...new Set(ids)]);
}

export interface SteamTagsBatch {
  total: number;
  offset: number;
  processed: number;
  updated: number;
  done: boolean;
}

// Re-reads the genres of the Steam games from the tags players vote on. The
// server does a few at a time, so ask again with the next offset until `done`.
export async function refreshSteamTags(
  offset: number,
  limit = 10,
): Promise<SteamTagsBatch> {
  if (import.meta.env.VITE_USE_MOCK_DATA === "true") {
    return { total: 0, offset, processed: 0, updated: 0, done: true };
  }
  const response = await fetch(
    `/api/library-sync/steam-tags/refresh?offset=${offset}&limit=${limit}`,
    { method: "POST", credentials: "include" },
  );
  if (!response.ok) {
    throw new Error(
      `Could not update the tags: ${response.status} ${response.statusText}`,
    );
  }
  return await response.json();
}
