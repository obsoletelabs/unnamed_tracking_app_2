import { computed, onScopeDispose, ref, shallowRef, watch } from "vue";
import type { Ref } from "vue";
import { preferences } from "../state/preferences";
import {
  cancelMetadataSearch,
  metadataEvents,
  focusMetadataEntity,
  selectMetadataCandidate,
  startMetadataSearch,
} from "../services/metadata";
import type {
  MetadataCandidate,
  MetadataEvent,
  MetadataMediaType,
} from "../services/metadata";

export function normalizedMetadataQuery(value: string): string {
  return value.toLocaleLowerCase().replace(/[^\p{L}\p{N}]/gu, "");
}

export function matchesMetadataQuery(
  candidate: MetadataCandidate,
  query: string,
): boolean {
  const normalized = normalizedMetadataQuery(query);
  return [candidate.title, ...candidate.alternate_titles].some((title) =>
    normalizedMetadataQuery(title).includes(normalized),
  );
}

function retainMetadata(
  previous: MetadataCandidate | undefined,
  incoming: MetadataCandidate,
): MetadataCandidate {
  if (
    !previous ||
    previous.provider !== incoming.provider ||
    previous.external_id !== incoming.external_id ||
    previous.media_type !== incoming.media_type ||
    Object.entries(previous.provider_ids).some(
      ([namespace, identity]) =>
        incoming.provider_ids[namespace] !== undefined &&
        incoming.provider_ids[namespace] !== identity,
    )
  )
    return incoming;
  // Search identities and partial text patches contain empty defaults until
  // preloading finishes. Preserve known text; fresh nonempty values take priority.
  const fields = Object.entries(incoming.metadata).filter(
    ([, value]) =>
      value !== null &&
      value !== undefined &&
      value !== "" &&
      (!Array.isArray(value) || value.length > 0) &&
      (typeof value !== "object" || Object.keys(value).length > 0),
  );
  const year = incoming.year ?? incoming.metadata.year ?? previous.year;
  return {
    ...incoming,
    provider_ids: { ...previous.provider_ids, ...incoming.provider_ids },
    year,
    metadata: {
      ...previous.metadata,
      ...Object.fromEntries(fields),
      ...(year !== null ? { year } : {}),
    },
  };
}

export function useMetadataSearch(
  query: Ref<string>,
  mediaType: MetadataMediaType,
) {
  const known = shallowRef(new Map<string, MetadataCandidate>());
  const origins = new Map<string, { sessionId: string; eventId: number }>();
  const selected = ref<MetadataCandidate | null>(null);
  const searching = ref(false);
  const enrichingMedia = ref(false);
  const warnings = ref<string[]>([]);
  const error = ref<string | null>(null);
  const searchedQuery = ref<string | null>(null);
  const hasSearched = computed(() => searchedQuery.value === query.value.trim());
  const manualSearchHint = computed(() =>
    !preferences.value.metadata_automatic_search_enabled &&
    query.value.trim().length >= 2 && !hasSearched.value
      ? "Automatic search is off. Press Search or Enter to fetch metadata."
      : null,
  );
  let generation = 0;
  let activeSession: string | null = null;
  let stream: EventSource | null = null;
  let timer: ReturnType<typeof setTimeout> | null = null;
  let controller: AbortController | null = null;
  let lastEventId = 0;
  let automaticSession = false;

  const results = computed(() =>
    [...known.value.values()]
      .filter((candidate) => matchesMetadataQuery(candidate, query.value))
      .sort(
        (first, second) =>
          first.rank - second.rank || first.id.localeCompare(second.id),
      ),
  );

  function disconnect(cancel = true) {
    stream?.close();
    stream = null;
    controller?.abort();
    controller = null;
    if (cancel && activeSession)
      void cancelMetadataSearch(activeSession).catch(() => {});
    activeSession = null;
  }

  function listen(sessionId: string, expectedGeneration: number, after = 0) {
    lastEventId = after;
    stream = metadataEvents(sessionId, after);
    const events = [
      "result_added",
      "result_updated",
      "result_removed",
      "ranking_updated",
      "provider_failed",
      "search_completed",
      "search_degraded",
      "search_cancelled",
      "selection_enrichment_completed",
    ];
    for (const name of events) {
      stream.addEventListener(name, (message) => {
        if (generation !== expectedGeneration || activeSession !== sessionId)
          return;
        const event: MetadataEvent = JSON.parse(
          (message as MessageEvent<string>).data,
        );
        if (event.session_id !== sessionId) return;
        if (event.id <= lastEventId) return;
        lastEventId = event.id;
        if (event.result) {
          const next = new Map(known.value);
          const result = retainMetadata(
            next.get(event.result.id),
            event.result,
          );
          next.set(result.id, result);
          // Bound retained local candidates while keeping backspacing useful.
          while (next.size > 300) {
            const oldest = next.keys().next().value!;
            next.delete(oldest);
            origins.delete(oldest);
          }
          known.value = next;
          origins.set(event.result.id, { sessionId, eventId: event.id });
          if (selected.value?.id === result.id) selected.value = result;
        }
        if (event.event === "result_removed" && event.candidate_id) {
          const next = new Map(known.value);
          next.delete(event.candidate_id);
          origins.delete(event.candidate_id);
          known.value = next;
          if (
            selected.value?.id === event.candidate_id &&
            event.replacement_id
          ) {
            selected.value =
              known.value.get(event.replacement_id) ?? selected.value;
          }
        }
        if (event.event === "ranking_updated" && event.candidate_ids) {
          const next = new Map(known.value);
          event.candidate_ids.forEach((id, rank) => {
            const candidate = next.get(id);
            if (candidate) next.set(id, { ...candidate, rank });
          });
          known.value = next;
        }
        if (event.event === "provider_failed" && event.message) {
          warnings.value = [...new Set([...warnings.value, event.message])];
        }
        if (
          ["search_completed", "search_degraded", "search_cancelled"].includes(
            event.event,
          )
        ) {
          searching.value = false;
        }
        if (event.event === "selection_enrichment_completed")
          enrichingMedia.value = false;
      });
    }
    stream.onerror = () => {
      if (generation !== expectedGeneration) return;
      // EventSource reconnects with Last-Event-ID while the session is still valid.
      if (stream?.readyState === EventSource.CLOSED) {
        searching.value = false;
        error.value = "Search updates are unavailable. Try searching again.";
      }
    };
  }

  async function search(trigger: "explicit" | "automatic" = "explicit") {
    if (timer) clearTimeout(timer);
    timer = null;
    const expectedGeneration = ++generation;
    disconnect();
    selected.value = null;
    warnings.value = [];
    error.value = null;
    const currentQuery = query.value.trim();
    automaticSession = trigger === "automatic";
    if (
      automaticSession &&
      !preferences.value.metadata_automatic_search_enabled
    ) {
      searching.value = false;
      return;
    }
    if (currentQuery.length < 2) {
      searching.value = false;
      return;
    }
    searching.value = true;
    searchedQuery.value = currentQuery;
    controller = new AbortController();
    try {
      const session = await startMetadataSearch(
        currentQuery,
        mediaType,
        controller.signal,
        trigger,
      );
      if (generation !== expectedGeneration) {
        void cancelMetadataSearch(session.id).catch(() => {});
        return;
      }
      activeSession = session.id;
      listen(session.id, expectedGeneration);
    } catch (failure) {
      if (generation !== expectedGeneration) return;
      searching.value = false;
      if (!(failure instanceof DOMException && failure.name === "AbortError")) {
        error.value =
          failure instanceof Error
            ? failure.message
            : "Metadata search failed.";
      }
    }
  }

  async function select(candidate: MetadataCandidate) {
    automaticSession = false;
    const origin = origins.get(candidate.id);
    if (!origin)
      throw new Error("Search for this title again before selecting it.");
    if (timer) clearTimeout(timer);
    timer = null;
    const expectedGeneration = ++generation;
    disconnect(activeSession !== origin.sessionId);
    activeSession = origin.sessionId;
    searching.value = false;
    selected.value = candidate;
    enrichingMedia.value = true;
    // Subscribe before selection so fast enrichment events cannot be missed.
    listen(origin.sessionId, expectedGeneration, origin.eventId);
    try {
      const response = await selectMetadataCandidate(
        origin.sessionId,
        candidate.id,
      );
      const result = retainMetadata(known.value.get(response.id), response);
      if (generation === expectedGeneration) {
        // A completion/ranking event cannot invalidate useful response fields.
        // Only a newer patch for this selected identity takes precedence.
        const current = selected.value;
        const patchedAt = current
          ? origins.get(current.id)?.eventId
          : undefined;
        selected.value =
          current && patchedAt !== undefined && patchedAt > origin.eventId
            ? retainMetadata(result, current)
            : result;
        const next = new Map(known.value);
        next.set(selected.value.id, selected.value);
        known.value = next;
      }
      return result;
    } catch (failure) {
      if (generation === expectedGeneration) {
        enrichingMedia.value = false;
        error.value =
          failure instanceof Error ? failure.message : "Selection failed.";
      }
      throw failure;
    }
  }

  async function focus(
    title: string,
    providerIds: Record<string, string> = {},
  ) {
    automaticSession = false;
    if (timer) clearTimeout(timer);
    timer = null;
    const expectedGeneration = ++generation;
    disconnect();
    const session = await focusMetadataEntity(title, mediaType, providerIds);
    if (generation !== expectedGeneration) {
      void cancelMetadataSearch(session.id).catch(() => {});
      return;
    }
    activeSession = session.id;
    selected.value = session.results[0] ?? null;
    enrichingMedia.value = true;
    listen(session.id, expectedGeneration);
  }

  const stopWatch = watch(
    query,
    () => {
      ++generation; // Invalidate old events on the keystroke, before the debounce expires.
      disconnect();
      selected.value = null;
      enrichingMedia.value = false;
      searchedQuery.value = null;
      if (timer) clearTimeout(timer);
      timer = null;
      searching.value =
        preferences.value.metadata_automatic_search_enabled &&
        query.value.trim().length >= 2;
      if (searching.value)
        timer = setTimeout(() => void search("automatic"), 250);
    },
    { flush: "sync" },
  );

  const stopPreferencesWatch = watch(
    () => preferences.value.metadata_automatic_search_enabled,
    (enabled) => {
      if (enabled) return; // Enabling does not issue a request without a new keystroke.
      const hadAutomaticTimer = timer !== null;
      if (timer) clearTimeout(timer);
      timer = null;
      if (hadAutomaticTimer || automaticSession) {
        ++generation;
        disconnect();
        searching.value = false;
        enrichingMedia.value = false;
      }
    },
    { flush: "sync" },
  );

  function stop() {
    ++generation;
    if (timer) clearTimeout(timer);
    timer = null;
    disconnect();
    searching.value = false;
    enrichingMedia.value = false;
    selected.value = null;
  }

  onScopeDispose(() => {
    stopWatch();
    stopPreferencesWatch();
    stop();
  });

  return {
    results,
    searching,
    selected,
    enrichingMedia,
    warnings,
    error,
    hasSearched,
    manualSearchHint,
    search: () => search(),
    select,
    focus,
    stop,
  };
}
