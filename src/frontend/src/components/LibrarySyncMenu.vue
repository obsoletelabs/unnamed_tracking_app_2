<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import {
  CONNECTIONS,
  isConnectionConnected,
  useConnections,
} from "../composables/useConnections";
import type { ConnectionConfig } from "../composables/useConnections";
import { usePluginConnections } from "../composables/usePluginConnections";
import { pluginConnectionKey } from "../services/pluginConnections";
import {
  pluginConnections,
  refreshPluginExtensions,
} from "../state/pluginExtensions";
import type { PluginConnectionContribution } from "../state/pluginExtensions";

const emit = defineEmits<{ synced: [] }>();
const router = useRouter();
const state = useConnections();
const menu = ref<HTMLDetailsElement | null>(null);
const pluginState = usePluginConnections();
const builtIns = CONNECTIONS.filter((connection) => connection.syncProvider);
const plugins = computed(() =>
  pluginConnections.value.filter((connection) => connection.syncAction),
);

async function syncBuiltIn(connection: ConnectionConfig) {
  if (menu.value) menu.value.open = false;
  if (!isConnectionConnected(state.credentialStatus[connection.key])) {
    await router.push("/settings?section=connections");
    return;
  }
  if (connection.syncProvider)
    await state.handleSyncLibrary(connection.syncProvider);
  emit("synced");
}
function pluginKey(connection: PluginConnectionContribution): string {
  return pluginConnectionKey(connection.pluginId, connection.contributionId);
}
// The host runs the sync; an account that is not connected goes to Connections.
async function syncPlugin(connection: PluginConnectionContribution) {
  if (menu.value) menu.value.open = false;
  const key = pluginKey(connection);
  if (!pluginState.states[key]?.connected) {
    await router.push("/settings?section=connections");
    return;
  }
  await pluginState.sync(key, connection.label);
  emit("synced");
}
onMounted(() => {
  void pluginState.load();
  void state.loadCredentials();
  void refreshPluginExtensions({ background: true });
});
</script>

<template>
  <details
    ref="menu"
    class="library-sync-menu"
    @keydown.esc="menu && (menu.open = false)"
  >
    <summary class="ui-btn ui-btn-ghost">Sync / import</summary>
    <div class="sync-options">
      <button
        v-for="connection in builtIns"
        :key="connection.key"
        type="button"
        :disabled="
          state.credentialsLoading.value ||
          Boolean(
            connection.syncProvider &&
            state.librarySyncing[connection.syncProvider],
          )
        "
        @click="syncBuiltIn(connection)"
      >
        {{
          isConnectionConnected(state.credentialStatus[connection.key])
            ? "Sync"
            : "Connect"
        }}
        {{ connection.label }}
      </button>
      <button
        v-for="connection in plugins"
        :key="`${connection.pluginId}:${connection.contributionId}`"
        type="button"
        :disabled="
          pluginState.loading.value ||
          pluginState.isSyncing(pluginKey(connection))
        "
        @click="syncPlugin(connection)"
      >
        {{
          pluginState.states[pluginKey(connection)]?.connected
            ? "Sync"
            : "Connect"
        }}
        {{ connection.label }}
      </button>
      <RouterLink to="/settings?section=background-tasks"
        >Autosync settings</RouterLink
      >
    </div>
  </details>
</template>

<style scoped>
.library-sync-menu {
  position: relative;
}
summary {
  list-style: none;
  cursor: pointer;
}
summary::-webkit-details-marker {
  display: none;
}
.sync-options {
  position: absolute;
  right: 0;
  top: calc(100% + 8px);
  z-index: 30;
  display: grid;
  min-width: 230px;
  max-width: calc(100vw - 32px);
  padding: 6px;
  background: var(--ui-surface);
  border: 1px solid var(--ui-border);
  border-radius: var(--ui-radius-control);
  box-shadow: 0 8px 24px #0002;
}
.sync-options button,
.sync-options a {
  display: block;
  padding: 12px;
  background: transparent;
  border: 0;
  border-radius: var(--ui-radius-control);
  color: var(--ui-text);
  text-align: left;
  font: inherit;
  font-size: 0.85rem;
  cursor: pointer;
  text-decoration: none;
}
.sync-options button:hover,
.sync-options a:hover {
  background: var(--ui-accent-soft);
}
.sync-options button:disabled {
  opacity: 0.5;
  cursor: default;
}
.sync-options a {
  border-top: 1px solid var(--ui-border);
  color: var(--ui-accent-text);
}
</style>
