<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { RouterLink } from "vue-router";
import ConnectionManageDialog from "./ConnectionManageDialog.vue";
import PluginConnectionDialog from "./PluginConnectionDialog.vue";
import SteamConnectionDialog from "./SteamConnectionDialog.vue";
import {
  CONNECTIONS,
  isConnectionConnected,
  useConnections,
} from "../../composables/useConnections";
import type { ConnectionConfig } from "../../composables/useConnections";
import { SOURCE_VISUALS } from "../../composables/useMetadataSources";
import { dispatchPluginAction } from "../../services/pluginUi";
import { pluginConnectionStatus } from "../../services/pluginConnections";
import type { PluginConnectionStatus } from "../../services/pluginConnections";
import { pluginConnections } from "../../state/pluginExtensions";
import type { PluginConnectionContribution } from "../../state/pluginExtensions";

const state = useConnections();
const managing = ref<ConnectionConfig | null>(null);
const managingPlugin = ref<PluginConnectionContribution | null>(null);
const steamSignIn = ref(false);
const pluginStatus = ref<Record<string, PluginConnectionStatus>>({});

const FALLBACK = { bg: "#1f1f1f", fg: "#d1d5db", mark: "•" };

function markFor(name: string) {
  return SOURCE_VISUALS[name] ?? { ...FALLBACK, mark: name.slice(0, 2) };
}

function timeAgo(unix: number | null | undefined): string {
  if (!unix) return "Never synced";
  const s = Math.max(0, Math.floor(Date.now() / 1000 - unix));
  if (s < 60) return "Synced just now";
  if (s < 3600) return `Synced ${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `Synced ${Math.floor(s / 3600)}h ago`;
  return `Synced ${Math.floor(s / 86400)}d ago`;
}

interface Row {
  key: string;
  label: string;
  connected: boolean;
  error: boolean;
  detail: string;
  connection?: ConnectionConfig;
  plugin?: PluginConnectionContribution;
}

function pluginKey(item: PluginConnectionContribution): string {
  return `${item.pluginId}:${item.contributionId}`;
}

const builtInRows = computed<Row[]>(() =>
  CONNECTIONS.map((connection) => {
    const s = state.credentialStatus[connection.key];
    const connected = isConnectionConnected(s);
    const bits: string[] = [];
    // The server reports a game count of 0 for providers that were never
    // set up; only a connected account has a real count to show.
    if (connected) {
      if (s?.display_name) bits.push(s.display_name);
      if (s?.library_games != null)
        bits.push(
          `${s.library_games} ${s.library_games === 1 ? "Game" : "Games"}`,
        );
      if (connection.syncProvider) bits.push(timeAgo(s?.last_synced_at));
    }
    return {
      key: connection.key,
      label: connection.label,
      connection,
      connected,
      error: s?.status === "error",
      detail:
        bits.join(" · ") ||
        (s?.status === "error"
          ? (s.detail ?? "Connection error")
          : connection.summary),
    };
  }),
);

const pluginRows = computed<Row[]>(() =>
  pluginConnections.value.map((plugin) => {
    const status = pluginStatus.value[pluginKey(plugin)];
    return {
      key: pluginKey(plugin),
      label: plugin.label,
      plugin,
      connected: status?.connected ?? false,
      error: status?.error ?? false,
      detail:
        status?.display_name ||
        (status?.error ? "Status unavailable" : plugin.description) ||
        plugin.document.title,
    };
  }),
);

const rows = computed<Row[]>(() =>
  [...builtInRows.value, ...pluginRows.value].sort(
    (a, b) => Number(b.connected) - Number(a.connected),
  ),
);

async function loadPluginStatus() {
  await Promise.all(
    pluginConnections.value.map(async (item) => {
      if (!item.statusAction) return;
      let status: PluginConnectionStatus;
      try {
        status = pluginConnectionStatus(
          await dispatchPluginAction(item.pluginId, item.statusAction.id),
        );
      } catch {
        status = { connected: false, error: true };
      }
      pluginStatus.value = { ...pluginStatus.value, [pluginKey(item)]: status };
    }),
  );
}

function closePlugin() {
  managingPlugin.value = null;
  // the popup may have connected or disconnected the account
  void loadPluginStatus();
}

onMounted(() => void state.loadCredentials());
watch(pluginConnections, () => void loadPluginStatus(), { immediate: true });
</script>

<template>
  <section class="settings-section">
    <h2>Connections</h2>
    <p class="hint">
      The accounts your library is linked to. Connect, update or disconnect an
      account here. Metadata sources and API keys are under Library > Metadata.
      Set up periodic library sync in
      <RouterLink to="/settings?section=background-tasks"
        >Background tasks</RouterLink
      >.
    </p>
    <p v-if="state.loadError.value" class="error" role="alert">
      {{ state.loadError.value }}
    </p>

    <p v-if="state.credentialsLoading.value" class="hint">Loading…</p>
    <ul v-else class="rows">
      <li v-for="row in rows" :key="row.key" class="row">
        <span
          class="mark"
          :style="{
            background: markFor(row.label).bg,
            color: markFor(row.label).fg,
          }"
          >{{ markFor(row.label).mark }}</span
        >
        <span class="info">
          <strong>{{ row.label }}</strong>
          <span class="detail" :title="row.detail">{{ row.detail }}</span>
        </span>
        <span class="pill" :class="{ ok: row.connected, bad: row.error }">{{
          row.error ? "Error" : row.connected ? "Connected" : "Not connected"
        }}</span>
        <span class="controls">
          <button
            v-if="row.connection?.syncProvider && row.connected"
            type="button"
            class="btn"
            :disabled="state.librarySyncing[row.connection.syncProvider]"
            @click="state.handleSyncLibrary(row.connection.syncProvider)"
          >
            {{
              state.librarySyncing[row.connection.syncProvider]
                ? "Syncing…"
                : "Sync now"
            }}
          </button>
          <button
            type="button"
            class="btn ghost"
            :aria-label="`${row.connected ? 'Manage' : 'Connect'} ${row.label}`"
            @click="
              row.plugin
                ? (managingPlugin = row.plugin)
                : (managing = row.connection ?? null)
            "
          >
            {{ row.connected ? "Manage" : "Connect" }}
          </button>
        </span>
      </li>
    </ul>

    <ConnectionManageDialog
      v-if="managing"
      :connection="managing"
      :state="state"
      @close="managing = null"
      @sign-in="
        managing = null;
        steamSignIn = true;
      "
    />
    <SteamConnectionDialog
      v-if="steamSignIn"
      @close="steamSignIn = false"
      @changed="state.loadCredentials()"
    />
    <PluginConnectionDialog
      v-if="managingPlugin"
      :connection="managingPlugin"
      @close="closePlugin"
    />
  </section>
</template>

<style scoped>
.settings-section h2 {
  margin: 0 0 12px;
  font: var(--ui-weight-heading) var(--ui-font-heading)/1.4
    var(--ui-font-family);
  color: var(--ui-text);
}
.hint {
  color: var(--ui-faint);
  font-size: 0.85rem;
  margin: 0 0 8px;
}
.error {
  color: var(--ui-error);
  font-size: 0.85rem;
}
.rows {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.row {
  display: grid;
  grid-template-columns: 34px minmax(0, 1fr) auto auto;
  align-items: center;
  gap: 12px;
  padding: 10px 12px;
  background: var(--ui-surface);
  border: 1px solid #262626;
  border-radius: var(--ui-radius-control);
}
.mark {
  width: 34px;
  height: 34px;
  border-radius: 9px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  font-weight: 800;
  flex-shrink: 0;
}
.info {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
  color: var(--ui-text);
  font-size: 0.9rem;
}
.detail {
  color: var(--ui-faint);
  font-size: 0.78rem;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.info strong {
  overflow-wrap: anywhere;
}
.controls {
  display: flex;
  gap: 8px;
}
.pill {
  font-size: 0.72rem;
  font-weight: 700;
  padding: 3px 9px;
  border-radius: 999px;
  color: var(--ui-dim);
  background: color-mix(in srgb, var(--ui-text) 6%, transparent);
  white-space: nowrap;
}
.pill.ok {
  color: var(--ui-good);
  background: rgba(34, 197, 94, 0.12);
}
.pill.bad {
  color: var(--ui-error);
  background: rgba(220, 38, 38, 0.14);
}
.btn {
  background: var(--ui-accent);
  color: var(--ui-on-accent);
  border: none;
  border-radius: var(--ui-radius-control);
  padding: 7px 12px;
  font: inherit;
  font-size: 0.8rem;
  font-weight: 700;
  cursor: pointer;
  white-space: nowrap;
}
.btn.ghost {
  background: color-mix(in srgb, var(--ui-text) 6%, transparent);
  color: var(--ui-text);
}
.btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}
@media (max-width: 600px) {
  .row {
    grid-template-columns: 34px minmax(0, 1fr) auto;
    gap: 8px;
  }
  .controls {
    grid-column: 2 / -1;
    justify-content: flex-end;
  }
}
</style>
