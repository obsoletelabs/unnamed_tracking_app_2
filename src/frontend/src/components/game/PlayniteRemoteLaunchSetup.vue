<script setup lang="ts">
import UiModal from "../UiModal.vue";
import {
  MINIMUM_REMOTE_LAUNCH_VERSION,
  type PlayniteConnection,
} from "../../services/remoteLaunch";
defineProps<{ connections: PlayniteConnection[] }>();
defineEmits<{ close: [] }>();
</script>

<template>
  <UiModal
    title="Set up remote start in Playnite"
    description="Regular library sync still works with older extensions. Remote start needs an updated extension and separate permission on each PC."
    @close="$emit('close')"
  >
    <ul v-if="connections.length" class="playnite-connections">
      <li v-for="connection in connections" :key="connection.id">
        <strong>{{ connection.name }}</strong>
        <span v-if="connection.support === 'outdated'"
          >Version {{ connection.version }} · Update required for remote
          start.</span
        >
        <span v-else-if="connection.support === 'supported'"
          >Version {{ connection.version }} · Supports remote start.</span
        >
        <span v-else
          >Version not reported. Older extensions do not report it; sync after
          updating to check.</span
        >
      </li>
    </ul>
    <ol class="playnite-setup-steps">
      <li>
        Install Unnamed Tracking for Playnite
        <strong>{{ MINIMUM_REMOTE_LAUNCH_VERSION }} or newer</strong> on the PC,
        then restart Playnite.
      </li>
      <li>
        In the extension settings, click
        <strong>Enable remote launch</strong> and approve the permission in your
        browser. Confirmation on the PC is on by default.
      </li>
      <li>
        Sync the game and keep Playnite open on the awake PC. Return here and
        choose that computer.
      </li>
    </ol>
    <p class="playnite-setup-note">
      Updating does not switch receiving on automatically. Remote start launches
      on the selected PC; it does not stream the game.
    </p>
    <template #footer>
      <a
        class="ui-btn ui-btn-primary"
        href="https://github.com/obsoletelabs/unnamed_tracking_playnite/releases"
        target="_blank"
        rel="noopener noreferrer"
        >Playnite extension downloads ↗</a
      >
      <button
        class="ui-btn ui-btn-secondary"
        type="button"
        @click="$emit('close')"
      >
        Got it
      </button>
    </template>
  </UiModal>
</template>

<style scoped>
.playnite-connections {
  margin: 0 0 20px;
  padding: 0;
  list-style: none;
  display: grid;
  gap: 12px;
}
.playnite-connections li {
  border: 1px solid var(--ui-border);
  border-radius: var(--ui-radius-control);
  padding: 12px 14px;
  overflow-wrap: anywhere;
}
.playnite-connections strong,
.playnite-connections span {
  display: block;
}
.playnite-connections span,
.playnite-setup-note {
  color: var(--ui-dim);
  font-size: var(--ui-font-small);
}
.playnite-setup-steps {
  padding-left: 24px;
  display: grid;
  gap: 12px;
}
.playnite-setup-note {
  margin-bottom: 0;
}
</style>
