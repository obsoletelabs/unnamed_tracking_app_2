import { mockGames } from "../data/mockGames";
import type { Game, GameStatus } from "../types/game";
import { failedRequest } from "./apiError";
import { denormalizeStatus, mapBackendGame } from "./games";
import type { BackendGame } from "./games";

// The light game edit changes only these fields; the PATCH leaves every other
// field alone, so protected and unrelated saved data cannot be overwritten.
export interface GameQuickEdit {
  status: GameStatus;
  platform: string | null;
  ratingOverall: number | null;
}

export async function quickUpdateGame(
  id: string,
  edit: GameQuickEdit,
): Promise<Game> {
  if (import.meta.env.VITE_USE_MOCK_DATA === "true") {
    const index = mockGames.findIndex((g) => g.id === id);
    if (index === -1) throw new Error(`Game ${id} not found`);
    mockGames[index] = {
      ...mockGames[index]!,
      status: edit.status,
      platform: edit.platform ?? "",
      ratingOverall: edit.ratingOverall,
    };
    return mockGames[index]!;
  }
  const response = await fetch(`/api/game/update/${id}`, {
    method: "PATCH",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      status: denormalizeStatus(edit.status),
      platform: edit.platform ?? "",
      rating_overall: edit.ratingOverall,
    }),
  });
  if (!response.ok) throw await failedRequest(response);
  return mapBackendGame((await response.json()) as BackendGame);
}
