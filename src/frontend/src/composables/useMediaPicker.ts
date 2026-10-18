// Server-side title search for the calendar's pickers. The libraries are
// paged on the server, so a preloaded array silently misses everything past
// the first page; this asks the existing paged list endpoints (movies, TV,
// anime) for matches instead, and reports empty / loading / error apart.
import { computed, onScopeDispose, ref, watch } from "vue";
import type { Ref } from "vue";
import type { MediaType } from "../services/mediaExtras";
import { fetchMoviesPage } from "../services/movies";
import { fetchTVShowsPage } from "../services/tvShows";
import { fetchAnimePage } from "../services/anime";
import { displayTitle } from "../utils/displayTitle";

export interface PickableMedia {
  mediaType: MediaType;
  mediaId: string;
  title: string;
  posterUrl: string | null;
}

export const PICKER_PAGE_SIZE = 8;
export const PICKER_MIN_CHARS = 2;
const PICKER_DEBOUNCE_MS = 250;

export async function searchOwnedMedia(
  query: string,
): Promise<PickableMedia[]> {
  const [movies, shows, anime] = await Promise.all([
    fetchMoviesPage(0, PICKER_PAGE_SIZE, query),
    fetchTVShowsPage(0, PICKER_PAGE_SIZE, query),
    fetchAnimePage(0, PICKER_PAGE_SIZE, query),
  ]);
  return [
    ...movies.items.map((m) => ({
      mediaType: "movie" as const,
      mediaId: m.id,
      title: m.title,
      posterUrl: m.posterUrl,
    })),
    ...shows.items.map((s) => ({
      mediaType: "tv" as const,
      mediaId: s.id,
      title: s.title,
      posterUrl: s.posterUrl,
    })),
    ...anime.items.map((a) => ({
      mediaType: "anime" as const,
      mediaId: a.id,
      title: displayTitle(a),
      posterUrl: a.posterUrl,
    })),
  ];
}

export type PickerState =
  "idle" | "too-short" | "loading" | "error" | "empty" | "results";

export function useMediaPicker(
  query: Ref<string>,
  picked: Ref<PickableMedia | null>,
  search: (q: string) => Promise<PickableMedia[]> = searchOwnedMedia,
) {
  const results = ref<PickableMedia[]>([]);
  const loading = ref(false);
  const error = ref<string | null>(null);
  let timer: ReturnType<typeof setTimeout> | undefined;
  let sequence = 0;

  async function run(text: string) {
    const mine = ++sequence;
    loading.value = true;
    error.value = null;
    try {
      const found = await search(text);
      if (mine !== sequence) return;
      results.value = found;
    } catch (e) {
      if (mine !== sequence) return;
      results.value = [];
      error.value = e instanceof Error ? e.message : "Search failed.";
    } finally {
      if (mine === sequence) loading.value = false;
    }
  }
  watch(query, (value) => {
    clearTimeout(timer);
    const text = value.trim();
    if (picked.value || text.length < PICKER_MIN_CHARS) {
      sequence++; // drop any search still in flight
      results.value = [];
      loading.value = false;
      error.value = null;
      return;
    }
    loading.value = true;
    error.value = null;
    timer = setTimeout(() => void run(text), PICKER_DEBOUNCE_MS);
  });
  onScopeDispose(() => clearTimeout(timer));

  const state = computed<PickerState>(() => {
    if (picked.value) return "idle";
    const text = query.value.trim();
    if (!text) return "idle";
    if (text.length < PICKER_MIN_CHARS) return "too-short";
    if (loading.value) return "loading";
    if (error.value) return "error";
    return results.value.length ? "results" : "empty";
  });
  function retry() {
    clearTimeout(timer);
    void run(query.value.trim());
  }
  return { results, loading, error, state, retry };
}
