import { failedRequest } from "./apiError";

export interface DuplicateGame {
  id: string;
  title: string;
  source: string | null;
  platform: string | null;
  release_date: string | null;
  external_id: string | null;
  provider_ids: Record<string, string>;
  status: string;
  playtime_seconds: number;
  locked_fields: string[];
}

export interface DuplicatePair {
  first: DuplicateGame;
  second: DuplicateGame;
  reason: "same_title" | "shared_identity";
}

export interface DuplicateSuggestions {
  pairs: DuplicatePair[];
  has_more: boolean;
}

export async function fetchGameDuplicates(
  signal?: AbortSignal,
): Promise<DuplicateSuggestions> {
  const response = await fetch("/api/game-duplicates", {
    credentials: "include",
    signal,
  });
  if (!response.ok) throw await failedRequest(response);
  return response.json();
}

export async function keepDuplicateGames(pair: DuplicatePair): Promise<void> {
  const response = await fetch("/api/game-duplicates/keep-both", {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      first_id: pair.first.id,
      second_id: pair.second.id,
    }),
  });
  if (!response.ok) throw await failedRequest(response);
}
