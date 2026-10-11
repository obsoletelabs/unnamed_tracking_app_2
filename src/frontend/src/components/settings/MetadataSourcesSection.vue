<script setup lang="ts">
import MetadataProviderSettings from "../plugins/MetadataProviderSettings.vue";
</script>

<template>
  <section class="settings-section">
    <h2>Metadata/API</h2>
    <p class="section-hint">
      Search metadata and artwork through built-in and optional providers.
      Expand a tile to configure your account credentials or view its validation
      status. Accounts you import a library from, such as Steam, PlayStation and
      RetroAchievements, are managed under
      <router-link to="/settings?area=account&section=connections"
        >Account &gt; Connections</router-link
      >.
    </p>
    <MetadataProviderSettings />
  </section>
</template>

<style scoped src="../../styles/pages/metadata-sources.css" />
