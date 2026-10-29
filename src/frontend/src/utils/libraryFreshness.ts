import { gameLibraryRevision } from "../services/gameCache";

// Remembers when a kept-alive library last loaded and which library revision
// it saw, so returning to it refetches after the max age or as soon as a game
// was changed or deleted elsewhere (e.g. on its detail page).
export function libraryFreshness(maxAgeMs = 60_000) {
  let loadedAt = 0;
  let loadedRevision = -1;
  return {
    // Call before fetching; invoke the result once the fetch has succeeded.
    begin() {
      const revision = gameLibraryRevision();
      return () => {
        loadedAt = Date.now();
        loadedRevision = revision;
      };
    },
    isStale: () =>
      loadedRevision !== gameLibraryRevision() ||
      Date.now() - loadedAt >= maxAgeMs,
  };
}
