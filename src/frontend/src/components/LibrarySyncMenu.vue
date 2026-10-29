<script setup lang="ts">
import { computed, defineAsyncComponent, ref } from "vue";
import { useRouter } from "vue-router";
import {
  CONNECTIONS,
  isConnectionConnected,
  useConnections,
} from "../composables/useConnections";
import type { ConnectionConfig } from "../composables/useConnections";
import {
  pluginConnections,
  refreshPluginExtensions,
} from "../state/pluginExtensions";
import type { PluginConnectionContribution } from "../state/pluginExtensions";
const PluginConnectionDialog = defineAsyncComponent(
  () => import("./settings/PluginConnectionDialog.vue"),
);

const emit = defineEmits<{ synced: [] }>();
const router = useRouter();
const state = useConnections();
const menu = ref<HTMLDetailsElement | null>(null);
const managing = ref<PluginConnectionContribution | null>(null);
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
function syncPlugin(connection: PluginConnectionContribution) {
  if (menu.value) menu.value.open = false;
  managing.value = connection;
}
function closePlugin() {
  managing.value = null;
  emit("synced");
}
function onToggle() {
  if (!menu.value?.open) return;
  void state.loadCredentials();
  void refreshPluginExtensions({ background: true });
}
</script>

<template>
  <details
    ref="menu"
    class="library-sync-menu"
    @toggle="onToggle"
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
        @click="syncPlugin(connection)"
      >
        Sync {{ connection.label }}
      </button>
      <RouterLink to="/settings?section=background-tasks"
        >Autosync settings</RouterLink
      >
    </div>
  </details>
  <PluginConnectionDialog
    v-if="managing"
    :connection="managing"
    sync-on-open
    @close="closePlugin"
  />
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
