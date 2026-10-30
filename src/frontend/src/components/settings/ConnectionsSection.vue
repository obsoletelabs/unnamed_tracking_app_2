<script setup lang="ts">
import { computed, defineAsyncComponent, onMounted, ref, watch } from "vue";
import { RouterLink, useRoute, useRouter } from "vue-router";
const ConnectionManageDialog = defineAsyncComponent(
  () => import("./ConnectionManageDialog.vue"),
);
const PluginConnectionDialog = defineAsyncComponent(
  () => import("./PluginConnectionDialog.vue"),
);
const SteamConnectionDialog = defineAsyncComponent(
  () => import("./SteamConnectionDialog.vue"),
);
import {
  CONNECTION_BY_KEY,
  CONNECTIONS,
  isConnectionConnected,
  useConnections,
} from "../../composables/useConnections";
import type { ConnectionConfig } from "../../composables/useConnections";
import { SOURCE_VISUALS } from "../../composables/useMetadataSources";
import { usePluginConnections } from "../../composables/usePluginConnections";
import { steamOpenIdOutcome } from "../../services/steamOpenId";
import { pluginConnectionKey } from "../../services/pluginConnections";
import { pluginConnections } from "../../state/pluginExtensions";
import type { PluginConnectionContribution } from "../../state/pluginExtensions";

const state = useConnections();
const plugins = usePluginConnections();
const route = useRoute();
const router = useRouter();
const steamOpenIdResult = ref<{ ok: boolean; message: string } | null>(null);
const managing = ref<ConnectionConfig | null>(null);
const managingPlugin = ref<PluginConnectionContribution | null>(null);
const steamSignIn = ref(false);

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
  return pluginConnectionKey(item.pluginId, item.contributionId);
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
    const status = plugins.states[pluginKey(plugin)];
    const bits: string[] = [];
    if (status?.connected) {
      if (status.display_name) bits.push(status.display_name);
      if (status.sync)
        bits.push(
          status.sync.status === "failed"
            ? "Last sync failed"
            : timeAgo(status.sync.last_run_at),
        );
    }
    return {
      key: pluginKey(plugin),
      label: plugin.label,
      plugin,
      connected: status?.connected ?? false,
      // without a status the host could not be reached, so don't claim "Not connected"
      error: status?.error ?? plugins.loadError.value !== null,
      detail:
        bits.join(" · ") ||
        (status?.error || (!status && plugins.loadError.value)
          ? "Status unavailable"
          : plugin.description) ||
        plugin.document.title,
    };
  }),
);

// A failed credentials read must not list every built-in account as "Not connected".
const rows = computed<Row[]>(() =>
  [
    ...(state.credentialsLoaded.value ? builtInRows.value : []),
    ...pluginRows.value,
  ].sort((a, b) => Number(b.connected) - Number(a.connected)),
);

function closePlugin() {
  managingPlugin.value = null;
  // the popup may have connected or disconnected the account
  void plugins.load();
}

function open(row: Row) {
  if (row.plugin) {
    managingPlugin.value = row.plugin;
    return;
  }
  // a new Steam connection goes straight to sign-in; Manage still opens the
  // full dialog with import, legacy credentials and disconnect
  if (row.connection?.signIn && !row.connected) steamSignIn.value = true;
  else managing.value = row.connection ?? null;
}

function openSteamLegacy() {
  steamSignIn.value = false;
  managing.value = CONNECTION_BY_KEY.Steam ?? null;
}

function syncOf(row: Row) {
  return row.plugin ? plugins.states[pluginKey(row.plugin)]?.sync : null;
}

function pluginSyncLabel(row: Row): string {
  return row.plugin && plugins.isSyncing(row.key) ? "Syncing…" : "Sync now";
}

function pluginSyncBlocked(row: Row): string {
  const sync = syncOf(row);
  if (!sync) return "";
  if (!sync.available) return sync.unavailable_reason;
  return sync.paused ? "This sync is paused in Background tasks." : "";
}

function pillText(row: Row): string {
  if (row.error) return "Error";
  if (!row.connected) return "Not connected";
  // nothing is pulled from deferred providers yet, so don't claim a link
  return row.connection?.deferred ? "Saved" : "Connected";
}

onMounted(() => {
  void state.loadCredentials();
  const outcome = steamOpenIdOutcome(route.query);
  if (!outcome) return;
  steamOpenIdResult.value = outcome;
  // The callback URL carries only a status; drop it so a reload does not repeat it.
  void router.replace({ query: { section: "connections" } });
});
// Status and sync state come from the host; reload when the installed set changes.
watch(pluginConnections, () => void plugins.load(), { immediate: true });
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
      <button
        v-if="!state.credentialsLoaded.value"
        type="button"
        class="btn ghost retry"
        :disabled="state.credentialsLoading.value"
        @click="state.retryCredentials()"
      >
        Retry
      </button>
    </p>

    <p
      v-if="steamOpenIdResult"
      :class="steamOpenIdResult.ok ? 'hint' : 'error'"
      :role="steamOpenIdResult.ok ? 'status' : 'alert'"
    >
      {{ steamOpenIdResult.message }}
    </p>
    <p v-if="plugins.loadError.value" class="error" role="alert">
      {{ plugins.loadError.value }}
      <button type="button" class="btn ghost retry" @click="plugins.load()">
        Retry
      </button>
    </p>
    <p v-if="state.credentialsLoading.value" class="hint">Loading…</p>
    <ul v-else class="rows">
      <li v-for="row in rows" :key="row.key" class="row">
        <span
          class="mark"
          aria-hidden="true"
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
          pillText(row)
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
            v-if="row.plugin && row.connected && syncOf(row)"
            type="button"
            class="btn"
            :disabled="
              plugins.isSyncing(row.key) || Boolean(pluginSyncBlocked(row))
            "
            :title="pluginSyncBlocked(row) || undefined"
            @click="plugins.sync(row.key, row.label)"
          >
            {{ pluginSyncLabel(row) }}
          </button>
          <button
            type="button"
            class="btn ghost"
            :aria-label="`${row.connected ? 'Manage' : 'Connect'} ${row.label}`"
            @click="open(row)"
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
      @legacy="openSteamLegacy"
    />
    <PluginConnectionDialog
      v-if="managingPlugin"
      :connection="managingPlugin"
      :connected="plugins.states[pluginKey(managingPlugin)]?.connected ?? false"
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
  border: 1px solid var(--ui-border-soft);
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
.retry {
  margin-left: 8px;
  padding: 3px 9px;
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
