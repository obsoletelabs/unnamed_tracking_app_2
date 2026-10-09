import { computed, ref } from "vue";
import type { Ref } from "vue";
import { useMetadataSearch } from "./useMetadataSearch";
import type { MetadataCandidate } from "../services/metadata";

export type MediaSearchType = "movie" | "tv_show" | "anime";
export const MEDIA_SEARCH_TYPES: MediaSearchType[] = [
  "movie",
  "tv_show",
  "anime",
];
const TYPE_LABELS = { movie: "Movies", tv_show: "TV shows", anime: "Anime" };

export function isMediaSearchType(value: unknown): value is MediaSearchType {
  return value === "movie" || value === "tv_show" || value === "anime";
}

// Each type keeps the existing progressive session, cancellation and identity
// rules. Aggregation never starts another provider request when a filter changes.
export function useMediaSearch(query: Ref<string>) {
  const searches = {
    movie: useMetadataSearch(query, "movie"),
    tv_show: useMetadataSearch(query, "tv_show"),
    anime: useMetadataSearch(query, "anime"),
  };
  const filter = ref<MediaSearchType | "all">("all");
  const selectedType = ref<MediaSearchType | null>(null);
  const results = computed(() =>
    MEDIA_SEARCH_TYPES.flatMap((type) =>
      filter.value === "all" || filter.value === type
        ? searches[type].results.value
        : [],
    ).sort(
      (a, b) =>
        a.rank - b.rank ||
        a.title.localeCompare(b.title) ||
        a.id.localeCompare(b.id),
    ),
  );
  const searching = computed(() =>
    MEDIA_SEARCH_TYPES.some((type) => searches[type].searching.value),
  );
  const warnings = computed(() => [
    ...new Set(
      MEDIA_SEARCH_TYPES.flatMap((type) =>
        [
          ...searches[type].warnings.value,
          ...(searches[type].error.value ? [searches[type].error.value!] : []),
        ].map((warning) => `${TYPE_LABELS[type]}: ${warning}`),
      ),
    ),
  ]);
  const selected = computed(() =>
    selectedType.value ? searches[selectedType.value].selected.value : null,
  );
  const enrichingMedia = computed(() =>
    selectedType.value
      ? searches[selectedType.value].enrichingMedia.value
      : false,
  );
  async function select(candidate: MetadataCandidate) {
    if (!isMediaSearchType(candidate.media_type)) return;
    const type = candidate.media_type;
    for (const other of MEDIA_SEARCH_TYPES)
      if (other !== type) searches[other].stop();
    selectedType.value = type;
    await searches[type].select(candidate);
  }
  function closeSelection() {
    if (selectedType.value) searches[selectedType.value].stop();
    selectedType.value = null;
  }
  function stop() {
    MEDIA_SEARCH_TYPES.forEach((type) => searches[type].stop());
    selectedType.value = null;
  }
  return {
    results,
    searching,
    warnings,
    selected,
    enrichingMedia,
    filter,
    select,
    closeSelection,
    stop,
    search: () =>
      Promise.all(MEDIA_SEARCH_TYPES.map((type) => searches[type].search())),
  };
}
