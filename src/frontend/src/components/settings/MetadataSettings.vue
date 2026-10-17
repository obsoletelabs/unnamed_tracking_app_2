<script setup lang="ts">
import { ref } from "vue";
import SettingsTabs from "./SettingsTabs.vue";
import MetadataSourcesSection from "./MetadataSourcesSection.vue";
import ScanSettingsSection from "./ScanSettingsSection.vue";
import SteamTagsSection from "./SteamTagsSection.vue";
import SteamWishlistSection from "./SteamWishlistSection.vue";
import MediaRefreshSection from "./MediaRefreshSection.vue";
import { METADATA_SETTINGS_TABS as TABS } from "../../state/settingsRegistry";

const props = defineProps<{ initialTab?: string }>();

const tab = ref(
  TABS.some((t) => t.id === props.initialTab) ? props.initialTab! : "sources",
);
</script>

<template>
  <div>
    <SettingsTabs v-model="tab" :tabs="TABS" />
    <MetadataSourcesSection v-if="tab === 'sources'" />
    <template v-else-if="tab === 'scan'">
      <ScanSettingsSection />
      <SteamTagsSection />
      <SteamWishlistSection />
    </template>
    <MediaRefreshSection v-else />
  </div>
</template>
