<script setup lang="ts">
import { computed } from "vue";
import UiModal from "../UiModal.vue";
import PluginContributionHost from "../plugins/PluginContributionHost.vue";
import type { PluginConnectionContribution } from "../../state/pluginExtensions";

const props = defineProps<{
  connection: PluginConnectionContribution;
  connected?: boolean;
}>();
const emit = defineEmits<{ close: [] }>();
const verb = computed(() => (props.connected ? "Manage" : "Connect"));
</script>

<template>
  <UiModal
    :title="`${verb} ${connection.label}`"
    :description="connection.description || connection.document.title"
    @close="emit('close')"
  >
    <PluginContributionHost
      :plugin-id="connection.pluginId"
      :document="connection.document"
      :page-id="connection.pageId"
      :context="{
        host_page: 'connections',
        initial_action_id: '',
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
