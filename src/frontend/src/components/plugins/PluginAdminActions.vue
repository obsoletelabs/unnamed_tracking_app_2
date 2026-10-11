<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { currentUser } from "../../state/auth";
import {
  pluginSlots,
  refreshPluginExtensions,
} from "../../state/pluginExtensions";
import UiModal from "../UiModal.vue";
import PluginContributionHost from "./PluginContributionHost.vue";

const selected = ref<string | null>(null);
const actions = computed(() =>
  currentUser.value?.is_admin
    ? pluginSlots.value.filter(
        (item) => item.slot === "administration.users.actions",
      )
    : [],
);
const key = (item: (typeof actions.value)[number]) =>
  `${item.pluginId}:${item.extensionId}`;
const active = computed(() =>
  actions.value.find((item) => key(item) === selected.value),
);
watch(active, (value) => {
  if (!value) selected.value = null;
});
onMounted(() => void refreshPluginExtensions({ background: true }));
</script>

<template>
  <div v-if="actions.length" class="plugin-admin-actions">
    <button
      v-for="item in actions"
      :key="key(item)"
      type="button"
      class="ui-button"
      @click="selected = key(item)"
    >
      {{ item.page.title }}
    </button>
    <UiModal
      v-if="active"
      :title="active.page.title"
      size="wide"
      @close="selected = null"
    >
      <PluginContributionHost
        :plugin-id="active.pluginId"
        :document="active.document"
        :page-id="active.page.id"
      />
    </UiModal>
  </div>
</template>

<style scoped>
.plugin-admin-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  margin: 20px 0;
}
</style>
