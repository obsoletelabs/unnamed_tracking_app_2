import { ref, watch, onBeforeUnmount, type Ref } from "vue";
import { currentUser } from "../state/auth";
import {
  fetchCalendarSources,
  fetchSourceEvents,
  type ExternalCalendarEvent,
} from "../services/calendarSources";

export function useCalendarSources(range: Ref<{ start: string; end: string }>) {
  const events = ref<ExternalCalendarEvent[]>([]);
  const error = ref("");
  const notices = ref<string[]>([]);
  const loading = ref(false);
  let generation = 0;
  let account = currentUser.value?.id;
  const cache = new Map<string, ExternalCalendarEvent[]>();
  async function load() {
    const ticket = ++generation;
    const owner = currentUser.value?.id;
    const window = { ...range.value };
    loading.value = true;
    error.value = "";
    try {
      const { sources } = await fetchCalendarSources();
      if (ticket !== generation || owner !== currentUser.value?.id) return;
      const active = sources.filter((s) => s.enabled);
      const results = await Promise.allSettled(
        active.map(async (source) => {
          const result = await fetchSourceEvents(
            source,
            window.start,
            window.end,
          );
          return { source, ...result };
        }),
      );
      if (ticket !== generation || owner !== currentUser.value?.id) return;
      const activeIds = new Set(active.map((s) => s.id));
      for (const id of cache.keys()) if (!activeIds.has(id)) cache.delete(id);
      const warnings = new Set<string>();
      results.forEach((result) => {
        if (result.status === "fulfilled") {
          const previous = (cache.get(result.value.source.id) ?? []).filter(
            (event) =>
              event.event_date > window.end || event.last_date < window.start,
          );
          const merged = new Map(
            previous.map((event) => [
              JSON.stringify([event.uid, event.recurrence_key]),
              event,
            ]),
          );
          for (const event of result.value.events)
            merged.set(JSON.stringify([event.uid, event.recurrence_key]), {
              ...event,
              source: result.value.source,
            });
          cache.set(result.value.source.id, [...merged.values()]);
          result.value.warnings.forEach((warning) => warnings.add(warning));
          if (result.value.source.consecutive_failures)
            warnings.add(
              "A source could not refresh; its last valid events are shown.",
            );
        } else
          error.value =
            "Some external calendars could not load. Previously loaded events are kept.";
      });
      events.value = [...cache.values()].flat();
      notices.value = [...warnings];
    } catch {
      if (ticket === generation)
        error.value =
          "External calendars could not load. Previously loaded events are kept.";
    } finally {
      if (ticket === generation) loading.value = false;
    }
  }
  watch(range, load);
  watch(
    () => currentUser.value?.id,
    (id) => {
      if (id === account) return;
      account = id;
      ++generation;
      cache.clear();
      events.value = [];
      error.value = "";
      notices.value = [];
      loading.value = false;
      if (id) void load();
    },
  );
  onBeforeUnmount(() => ++generation);
  return { events, error, notices, loading, load };
}
