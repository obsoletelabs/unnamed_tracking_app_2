<script setup lang="ts">
import UiModal from "../UiModal.vue";
import PluginContributionHost from "../plugins/PluginContributionHost.vue";
import type { PluginConnectionContribution } from "../../state/pluginExtensions";

defineProps<{
  connection: PluginConnectionContribution;
  syncOnOpen?: boolean;
}>();
const emit = defineEmits<{ close: [] }>();
</script>

<template>
  <UiModal
    :title="`${syncOnOpen ? 'Sync' : 'Connect'} ${connection.label}`"
    :description="connection.description || connection.document.title"
    @close="emit('close')"
  >
    <PluginContributionHost
      :plugin-id="connection.pluginId"
      :document="connection.document"
      :page-id="connection.pageId"
      :context="{
        host_page: 'connections',
        initial_action_id: syncOnOpen ? (connection.syncAction?.id ?? '') : '',
      }"
      inline
    />
    <template #footer>
      <button type="button" class="ui-btn ui-btn-ghost" @click="emit('close')">
        Close
      </button>
    </template>
  </UiModal>
</template>
