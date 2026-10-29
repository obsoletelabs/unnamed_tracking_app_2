import { onScopeDispose, reactive, ref } from "vue";

import {
  fetchPluginConnections,
  pluginConnectionKey,
  startPluginConnectionSync,
} from "../services/pluginConnections";
import type { PluginConnectionState } from "../services/pluginConnections";
import { completeTask, errorTask, startTask } from "../state/taskProgress";

export const SYNC_POLL_MS = 2000;
const MAX_POLL_FAILURES = 3;

const FAILED = new Set(["failed", "cancelled", "disabled"]);

// Plugin connection state is loaded by the host. A sync runs on the server, so
// the page only starts it and polls for the result.
export function usePluginConnections() {
  const states = reactive<Record<string, PluginConnectionState>>({});
  const loading = ref(true);
  const loadError = ref<string | null>(null);
  const starting = reactive<Record<string, boolean>>({});
  let polling: Promise<void> | null = null;
  let disposed = false;

  function apply(items: PluginConnectionState[]) {
    const seen = new Set<string>();
    for (const item of items) {
      const key = pluginConnectionKey(item.plugin_id, item.connection_id);
      seen.add(key);
      states[key] = item;
    }
    for (const key of Object.keys(states))
      if (!seen.has(key)) delete states[key];
  }

  async function load() {
    try {
      apply(await fetchPluginConnections());
      loadError.value = null;
    } catch (err) {
      loadError.value =
        err instanceof Error ? err.message : "Couldn't load connections.";
    } finally {
      loading.value = false;
    }
    if (anyRunning()) void pollUntilIdle();
  }

  function anyRunning(): boolean {
    return Object.values(states).some((item) => item.sync?.running);
  }

  function isSyncing(key: string): boolean {
    return Boolean(starting[key] || states[key]?.sync?.running);
  }

  // One loop serves every running sync, including one a different tab or the
  // schedule started. It gives up after repeated failures; the sync itself
  // keeps going on the server.
  function pollUntilIdle(): Promise<void> {
    polling ??= (async () => {
      let failures = 0;
      try {
        while (!disposed && anyRunning() && failures < MAX_POLL_FAILURES) {
          await new Promise((resolve) => setTimeout(resolve, SYNC_POLL_MS));
          try {
            apply(await fetchPluginConnections());
            failures = 0;
          } catch {
            failures += 1;
          }
        }
      } finally {
        polling = null;
      }
    })();
    return polling;
  }

  async function sync(key: string, label: string) {
    const item = states[key];
    if (!item || isSyncing(key)) return;
    starting[key] = true;
    const taskId = startTask(`Syncing ${label} library`, 1, {
      indeterminate: true,
    });
    try {
      item.sync = await startPluginConnectionSync(
        item.plugin_id,
        item.connection_id,
      );
      await pollUntilIdle();
      const sync = states[key]?.sync;
      if (!sync || sync.running) {
        errorTask(
          taskId,
          "Lost contact with the server. The sync keeps running; reload to check on it.",
        );
      } else if (FAILED.has(sync.status)) {
        errorTask(taskId, sync.last_summary || "Sync failed");
      } else {
        completeTask(taskId, sync.last_summary || "Sync complete");
      }
    } catch (err) {
      errorTask(taskId, err instanceof Error ? err.message : "Sync failed");
    } finally {
      starting[key] = false;
    }
  }

  onScopeDispose(() => {
    disposed = true;
  });

  return { states, loading, loadError, load, sync, isSyncing };
}

export type PluginConnectionsState = ReturnType<typeof usePluginConnections>;
