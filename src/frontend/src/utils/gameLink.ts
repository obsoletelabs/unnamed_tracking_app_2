export const GAME_REQUEST_TIMEOUT_MS = 15_000;

export function isGameId(value: unknown): value is string {
  return (
    typeof value === "string" &&
    /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(value)
  );
}

export const gameLinkStates = {
  "not-found": {
    title: "Game not found",
    description:
      "This game may have been removed, or you may not have access to it. You can find your available games in your library.",
  },
  invalid: {
    title: "Invalid game link",
    description:
      "This link is incomplete or is not a valid game address. Open your library to find the game you’re looking for.",
  },
  unavailable: {
    title: "We couldn’t load this game",
    description:
      "There may be a connection or server problem. Try again in a moment, or return to your library.",
  },
  "signed-out": {
    title: "Sign in to view this game",
    description:
      "Your session may have ended. Sign in again to continue to your library.",
  },
} as const;

export type GameLinkFailure = keyof typeof gameLinkStates;
