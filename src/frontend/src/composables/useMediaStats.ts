import {
  computed,
  onActivated,
  onDeactivated,
  onMounted,
  onScopeDispose,
  ref,
  watch,
} from "vue";
import type { Ref } from "vue";
import { fetchMediaStats, peekMediaStats } from "../services/mediaStats";
import type { MediaStats, MediaStatsSection } from "../services/mediaStats";
import { useKeptAlive } from "../utils/useKeptAlive";

export function useMediaStats(
  section: Ref<MediaStatsSection>,
  actor: Readonly<Ref<string | null>>,
) {
  const stats = ref<Partial<MediaStats>>(peekMediaStats(actor.value));
  const hasData = computed(() => Boolean(stats.value[section.value]));
  const loading = ref(false);
  const error = ref<string | null>(null);
  let active = true;
  let generation = 0;
  let request: AbortController | null = null;
  const freshSections = new Set<MediaStatsSection>();

  function cancel() {
    generation++;
    request?.abort();
    request = null;
    loading.value = false;
  }

  async function load(refresh = true) {
    cancel();
    error.value = null;
    if (!active || !actor.value) return;
    stats.value = peekMediaStats(actor.value);
    if (!refresh && hasData.value && freshSections.has(section.value)) return;
    const selected = section.value;
    const user = actor.value;
    const current = generation;
    const controller = new AbortController();
    request = controller;
    loading.value = true;
    try {
      const result = await fetchMediaStats(selected, user, controller.signal);
      if (
        current === generation &&
        actor.value === user &&
        section.value === selected
      ) {
        stats.value = { ...stats.value, ...result };
        freshSections.add(selected);
      }
    } catch (cause) {
      if (current === generation && !controller.signal.aborted)
        error.value =
          cause instanceof Error ? cause.message : "Failed to load statistics.";
    } finally {
      if (current === generation) {
        loading.value = false;
        request = null;
      }
    }
  }

  watch(
    [section, actor],
    ([, user], [, previousUser]) => {
      if (user !== previousUser) {
        stats.value = peekMediaStats(user);
        freshSections.clear();
      }
      void load(user !== previousUser);
    },
    { flush: "sync" },
  );
  onMounted(() => void load());
  onActivated(() => {
    active = true;
  });
  onDeactivated(() => {
    active = false;
    freshSections.clear();
    cancel();
  });
  onScopeDispose(cancel);
  useKeptAlive(() => void load());
  return { stats, hasData, loading, error, load };
}
