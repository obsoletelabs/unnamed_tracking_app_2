<script setup lang="ts">
import { onMounted, ref } from "vue";
import ToggleButton from "./ToggleButton.vue";
import {
  DEFAULT_PREFERENCES,
  fetchPreferences,
  queuePreferences,
} from "../../services/preferences";
import type { Preferences } from "../../services/preferences";

const prefs = ref<Preferences>({ ...DEFAULT_PREFERENCES });
const loaded = ref(false);
const error = ref<string | null>(null);
const saved = ref(false);

onMounted(async () => {
  try {
    prefs.value = await fetchPreferences();
  } catch (e) {
    error.value = e instanceof Error ? e.message : "Failed to load settings.";
  } finally {
    loaded.value = true;
  }
});

async function change(changes: Partial<Preferences>) {
  prefs.value = { ...prefs.value, ...changes };
  error.value = null;
  try {
    const { prefs: savedPrefs } = await queuePreferences(changes);
    prefs.value = savedPrefs;
    saved.value = true;
    setTimeout(() => (saved.value = false), 1500);
  } catch (e) {
    error.value = e instanceof Error ? e.message : "Failed to save.";
  }
}
</script>

<template>
  <section class="settings-section">
    <h2>AniList Import</h2>
    <p class="section-hint">
      Import your public AniList anime list into this library. The import only
      reads AniList and never changes it.
      <span v-if="saved" class="saved">Saved</span>
    </p>
    <p v-if="error" class="error">{{ error }}</p>
    <div class="field">
      <label for="anilist-username">AniList username</label>
      <input
        id="anilist-username"
        :value="prefs.anilist_import_username"
        maxlength="100"
        placeholder="Your AniList username"
        :disabled="!loaded"
        @change="
          change({
            anilist_import_username: ($event.target as HTMLInputElement).value,
          })
        "
      />
    </div>
    <ToggleButton
      :model-value="prefs.anilist_import_update_existing"
      label="Update existing titles"
      :disabled="!loaded"
      @update:model-value="change({ anilist_import_update_existing: $event })"
    >
      <strong>Update existing titles</strong>: apply AniList list changes to
      titles already in the library
    </ToggleButton>
    <p class="section-hint">
      The schedule lives in
      <RouterLink to="/settings?section=background-tasks"
        >Background tasks</RouterLink
      >
      under "AniList import". Turn it on there to import automatically, choose
      how often, run it now and see the last result.
    </p>
  </section>
</template>

<style scoped>
.settings-section h2 {
  margin: 0 0 12px;
  font: var(--ui-weight-heading) var(--ui-font-heading)/1.4
    var(--ui-font-family);
  color: var(--ui-text);
}
.section-hint {
  color: var(--ui-dim);
  font-size: 0.82rem;
  line-height: 1.6;
  margin: 0 0 14px;
}
.saved {
  color: var(--ui-good);
  margin-left: 8px;
  font-weight: 700;
}
.error {
  color: var(--ui-error);
  font-size: 0.82rem;
}
.field {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin: 14px 0;
  font-size: 0.82rem;
  color: var(--ui-text);
}
.field input {
  width: min(100%, 420px);
  box-sizing: border-box;
  padding: 9px 11px;
  border: 1px solid var(--ui-border-strong);
  border-radius: 7px;
  background: var(--ui-bg);
  color: var(--ui-text);
}
.settings-section :deep(.toggle-button) {
  margin: 12px 0;
}
</style>
