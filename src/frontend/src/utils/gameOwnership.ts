import type { Game } from "../types/game";

// Preserve original records and their metrics. Only the library presentation is
// collapsed; orphaned copies stay visible if their main is in Trash or missing.
export function gameOwnershipGroups(games: Game[]): {
  entries: Game[];
  members: Map<string, Game[]>;
} {
  const byId = new Map(games.map((game) => [game.id, game]));
  const members = new Map<string, Game[]>();
  const entries = games.filter((game) => {
    const parent = game.parentGameId ? byId.get(game.parentGameId) : undefined;
    return !(
      game.relationshipType === "owned_copy" &&
      parent &&
      !parent.parentGameId &&
      parent.id !== game.id
    );
  });
  for (const game of entries) members.set(game.id, [game]);
  for (const game of games) {
    if (game.relationshipType === "owned_copy" && game.parentGameId) {
      const group = members.get(game.parentGameId);
      if (group && group[0]?.id !== game.id) group.push(game);
    }
  }
  return { entries, members };
}
