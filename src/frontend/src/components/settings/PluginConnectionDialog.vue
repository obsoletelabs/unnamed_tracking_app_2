<script setup lang="ts">
import UiModal from "../UiModal.vue";
import PluginContributionHost from "../plugins/PluginContributionHost.vue";
import type { PluginConnectionContribution } from "../../state/pluginExtensions";

defineProps<{ connection: PluginConnectionContribution }>();
const emit = defineEmits<{ close: [] }>();
</script>

<template>
  <UiModal
    :title="connection.label"
    :description="connection.description || connection.document.title"
    size="wide"
    @close="emit('close')"
  >
    <PluginContributionHost
      :plugin-id="connection.pluginId"
      :document="connection.document"
      :page-id="connection.pageId"
      :context="{ host_page: 'connections' }"
      chromeless
    />
    <template #footer>
      <button type="button" class="ui-btn ui-btn-ghost" @click="emit('close')">
        Close
      </button>
    </template>
  </UiModal>
</template>
