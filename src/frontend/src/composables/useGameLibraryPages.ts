import { computed, ref, type Ref } from "vue";
import type { Game } from "../types/game";

// A page request belongs to one query generation. Cancellation and completion
// state live here so an obsolete reply cannot mutate a newer library load.
export function useGameLibraryPages(
  games: Ref<Game[]>,
  totalGameCount: Ref<number>,
  fetchPage: (
    offset: number,
    signal: AbortSignal,
  ) => Promise<{ games: Game[]; total: number | null }>,
  isQueryPending: () => boolean,
) {
  const loadingMoreGames = ref(false);
  const loadMoreError = ref<string | null>(null);
  const paginationStopped = ref(false);
  const nextGameOffset = ref(0);
  let pageRequest: AbortController | null = null;
  let pendingPageLoad: Promise<void> | null = null;
  let generation = 0;
  function resetPages() {
    ++generation;
    pageRequest?.abort();
    pageRequest = null;
    pendingPageLoad = null;
    loadingMoreGames.value = false;
    loadMoreError.value = null;
    paginationStopped.value = false;
    nextGameOffset.value = 0;
  }
  const hasMoreGames = computed(
    () =>
      !paginationStopped.value && nextGameOffset.value < totalGameCount.value,
  );

  function loadMoreGames(): Promise<void> {
    if (pendingPageLoad) return pendingPageLoad;
    if (isQueryPending() || !hasMoreGames.value) return Promise.resolve();
    const token = generation;
    const controller = new AbortController();
    pageRequest = controller;
    loadingMoreGames.value = true;
    loadMoreError.value = null;
    const request = (async () => {
      try {
        const result = await fetchPage(nextGameOffset.value, controller.signal);
        if (token !== generation || controller.signal.aborted) return;
        const seenIds = new Set(games.value.map((game) => game.id));
        const fresh = result.games.filter((game) => {
          if (seenIds.has(game.id)) return false;
          seenIds.add(game.id);
          return true;
        });
        if (result.total !== null) totalGameCount.value = result.total;
        if (!fresh.length) {
          paginationStopped.value = true;
          if (nextGameOffset.value < totalGameCount.value)
            loadMoreError.value =
              "The server returned no additional games. Retry to finish loading the library.";
          return;
        }
        games.value = [...games.value, ...fresh];
        nextGameOffset.value += result.games.length;
      } catch {
        if (token === generation && !controller.signal.aborted)
          loadMoreError.value = "Could not load more games. Try again.";
      } finally {
        if (token === generation && pageRequest === controller) {
          loadingMoreGames.value = false;
          pendingPageLoad = null;
        }
      }
    })();
    pendingPageLoad = request;
    return request;
  }

  function retryLoadMoreGames(): Promise<void> {
    paginationStopped.value = false;
    return loadMoreGames();
  }

  async function loadAllGames(): Promise<void> {
    const token = generation;
    while (token === generation && hasMoreGames.value) {
      const before = nextGameOffset.value;
      await loadMoreGames();
      if (nextGameOffset.value === before) break;
    }
  }

  return {
    nextGameOffset,
    hasMoreGames,
    loadingMoreGames,
    loadMoreError,
    loadMoreGames,
    retryLoadMoreGames,
    loadAllGames,
    resetPages,
  };
}
