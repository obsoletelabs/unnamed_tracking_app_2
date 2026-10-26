import { computed, onUnmounted, ref, type Ref } from "vue";
import { savedMediaSort, type LibraryFilters } from "../utils/libraryFilters";
import type { MediaPage } from "../services/mediaApi";

type Filters = LibraryFilters & { statusBucket: string };

// One query generation and refresh policy for all three media libraries.
export function useMediaLibraryPage<T extends { id: string }>(
  fetchPage: (
    offset: number,
    limit: number,
    filters: LibraryFilters,
  ) => Promise<MediaPage<T>>,
  kind: string,
  plural: string,
  mutationRevision: () => number = () => 0,
) {
  // Media entities are plain data; their generic type contains no Vue refs to unwrap.
  const entries = ref<T[]>([]) as Ref<T[]>;
  const loading = ref(true);
  const refreshing = ref(false);
  const error = ref<string | null>(null);
  const total = ref(0);
  const statusCounts = ref<Record<string, number>>({});
  const scoreRanks = ref<Record<string, number>>({});
  const currentFilters = ref<Filters>({
    search: "",
    genres: [],
    genreMatchAll: false,
    formats: [],
    onlyFavorites: false,
    onlyUnrated: false,
    onlyWithNote: false,
    minScore: null,
    yearFrom: "",
    yearTo: "",
    statusBucket: "all",
    sort: savedMediaSort(kind),
  });
  // Match the first DOM render batch. Larger libraries continue near the viewport.
  const pageSize = 48;
  let generation = 0;
  let lastLoadedAt = 0;
  let loadedRevision = -1;
  const paginationStopped = ref(false);
  const hasMore = computed(
    () => !paginationStopped.value && entries.value.length < total.value,
  );
  onUnmounted(() => {
    generation++;
  });

  function rememberPage(page: MediaPage<T>) {
    total.value = page.total;
    statusCounts.value = page.statusCounts;
    scoreRanks.value = page.scoreRanks;
  }

  async function load(
    filters: Filters = currentFilters.value,
    preserveExtent = false,
  ) {
    const sameQuery =
      JSON.stringify(filters) === JSON.stringify(currentFilters.value);
    const extent =
      preserveExtent && sameQuery
        ? Math.max(pageSize, entries.value.length)
        : pageSize;
    const query = {
      ...filters,
      genres: [...filters.genres],
      formats: [...filters.formats],
    };
    currentFilters.value = query;
    const request = ++generation;
    const revision = mutationRevision();
    const originalRows = new Map(
      entries.value.map((item) => [item.id, JSON.stringify(item)]),
    );
    refreshing.value = true;
    if (!entries.value.length) loading.value = true;
    try {
      const collected: T[] = [];
      const seen = new Set<string>();
      let offset = 0;
      let latest: MediaPage<T>;
      do {
        const limit = Math.min(pageSize, extent - offset);
        latest = await fetchPage(offset, limit, query);
        if (request !== generation) return;
        if (offset === 0) rememberPage(latest);
        const fresh = latest.items.filter((item) => {
          if (seen.has(item.id)) return false;
          seen.add(item.id);
          return true;
        });
        collected.push(...fresh);
        offset += latest.items.length;
        if (
          !fresh.length ||
          latest.items.length < limit ||
          offset >= latest.total
        )
          break;
      } while (offset < extent);
      const current = new Map(entries.value.map((item) => [item.id, item]));
      // A successful local save or delete during refresh owns that row's newer state.
      entries.value = collected
        .filter((item) => !originalRows.has(item.id) || current.has(item.id))
        .map((item) => {
          const local = current.get(item.id);
          return local &&
            originalRows.has(item.id) &&
            JSON.stringify(local) !== originalRows.get(item.id)
            ? local
            : item;
        });
      total.value = latest.total;
      paginationStopped.value = false;
      lastLoadedAt = Date.now();
      loadedRevision = revision;
      error.value = null;
    } catch (e) {
      if (request === generation)
        error.value =
          e instanceof Error ? e.message : `Failed to load ${plural}.`;
    } finally {
      if (request === generation) {
        loading.value = false;
        refreshing.value = false;
      }
    }
  }

  async function loadMore() {
    if (loading.value || refreshing.value || !hasMore.value) return;
    const request = generation;
    loading.value = true;
    try {
      const page = await fetchPage(
        entries.value.length,
        pageSize,
        currentFilters.value,
      );
      if (request !== generation) return;
      const seen = new Set(entries.value.map((item) => item.id));
      const fresh = page.items.filter((item) => {
        if (seen.has(item.id)) return false;
        seen.add(item.id);
        return true;
      });
      entries.value.push(...fresh);
      paginationStopped.value = !fresh.length;
      total.value = page.total;
      error.value = null;
    } catch (e) {
      if (request === generation)
        error.value =
          e instanceof Error ? e.message : `Failed to load more ${plural}.`;
    } finally {
      if (request === generation) loading.value = false;
    }
  }

  async function refreshLibrary() {
    if (
      error.value ||
      loadedRevision !== mutationRevision() ||
      Date.now() - lastLoadedAt >= 120_000
    )
      await load(currentFilters.value, true);
  }

  return {
    entries,
    loading,
    refreshing,
    error,
    total,
    statusCounts,
    scoreRanks,
    currentFilters,
    hasMore,
    load,
    loadMore,
    refreshLibrary,
  };
}
