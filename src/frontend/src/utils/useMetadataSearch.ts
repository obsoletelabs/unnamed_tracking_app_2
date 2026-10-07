// Form presentation adapts the one generic progressive search lifecycle.
import { computed, ref, watch } from "vue";
import { useMetadataSearch as useProgressiveSearch } from "../composables/useMetadataSearch";
import type {
  MetadataCandidate,
  MetadataMediaType,
} from "../services/metadata";
import { lockedFieldLabels } from "./lockedFields";

export function useMetadataSearch<
  R extends { title: string; provider: string; candidateId?: string },
>(options: {
  mediaType: MetadataMediaType;
  convert: (candidate: MetadataCandidate) => R;
  noun: string;
}) {
  const query = ref("");
  const progressive = useProgressiveSearch(query, options.mediaType);
  const appliedMessage = ref<string | null>(null);
  const selected = computed(() =>
    progressive.selected.value
      ? options.convert(progressive.selected.value)
      : null,
  );
  const results = computed(() =>
    selected.value ? [] : progressive.results.value.map(options.convert),
  );
  const message = computed(() => {
    if (progressive.error.value) return progressive.error.value;
    if (appliedMessage.value) return appliedMessage.value;
    if (
      progressive.searching.value ||
      query.value.trim().length < 2 ||
      results.value.length
    )
      return null;
    return `No ${options.noun === "show" ? "shows" : options.noun === "movie" ? "movies" : options.noun} found. Check installed providers in Settings > Metadata/API.`;
  });
  watch(query, () => {
    appliedMessage.value = null;
  });

  async function select(result: R) {
    const candidate = progressive.results.value.find(
      (item) => item.id === result.candidateId,
    );
    if (candidate) await progressive.select(candidate);
  }

  function applied(result: R, lockedFields: string[], note = "") {
    appliedMessage.value = lockedFields.length
      ? `Prefilled from ${result.provider}${note}. Skipped ${lockedFields.length} field(s) you've already edited: ${lockedFieldLabels(lockedFields).join(", ")}. Review the fields before saving.`
      : `Prefilled from ${result.provider}${note}. Review the fields before saving.`;
  }

  return {
    query,
    results,
    selected,
    searching: progressive.searching,
    message,
    warnings: progressive.warnings,
    enrichingMedia: progressive.enrichingMedia,
    run: progressive.search,
    select,
    applied,
  };
}
