<script setup lang="ts">
import { ref } from "vue";
import SettingsTabs from "./SettingsTabs.vue";
import LibraryManagementSection from "./LibraryManagementSection.vue";
import MediaPreferencesSection from "./MediaPreferencesSection.vue";
import MediaTrashSection from "./MediaTrashSection.vue";
import DuplicateGamesSection from "./DuplicateGamesSection.vue";

const props = defineProps<{ initialTab?: string }>();

const TABS = [
  { id: "manage", label: "Library" },
  { id: "preferences", label: "Preferences" },
  { id: "duplicates", label: "Duplicates" },
  { id: "trash", label: "Trash" },
];
const tab = ref(
  TABS.some((t) => t.id === props.initialTab) ? props.initialTab! : "manage",
);
</script>

<template>
  <div>
    <SettingsTabs v-model="tab" :tabs="TABS" />
    <LibraryManagementSection v-if="tab === 'manage'" />
    <MediaPreferencesSection v-else-if="tab === 'preferences'" />
    <DuplicateGamesSection v-else-if="tab === 'duplicates'" />
    <MediaTrashSection v-else />
  </div>
</template>
