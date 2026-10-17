<script setup lang="ts">
// How versions and related games appear in the game library (#101). Saved on
// the server as they are changed; nothing here changes any game's data.
import { onMounted, ref } from "vue";
import SegmentedControl from "./SegmentedControl.vue";
import {
  DEFAULT_PREFERENCES,
  fetchPreferences,
  queuePreferences,
} from "../../services/preferences";
import type { Preferences } from "../../services/preferences";
import { preferences as sharedPreferences } from "../../state/preferences";

const prefs = ref<Preferences>({ ...DEFAULT_PREFERENCES });
const loading = ref(true);
const error = ref<string | null>(null);
const savedNote = ref("");

const modeOptions = [
  { value: "grouped", label: "One entry per game" },
  { value: "separate", label: "Every version on its own" },
];
const KINDS: [string, string][] = [
  ["dlc_of", "DLC"],
  ["expansion_of", "Expansions"],
  ["standalone_expansion_of", "Standalone expansions"],
  ["mod_of", "Mods"],
  ["modpack_of", "Modpacks"],
  ["total_conversion_of", "Total conversions"],
  ["remake_of", "Remakes"],
  ["remaster_of", "Remasters"],
  ["port_of", "Ports"],
  ["version_of", "Other versions"],
  ["series_member", "Series members"],
  ["collection_member", "Collection members"],
  ["related_to", "Related games"],
];
const choices = [
  { value: "link", label: "List as its own game" },
  { value: "nest", label: "Show under the main game" },
  { value: "hidden", label: "Hide from the library" },
];

async function load(): Promise<void> {
  loading.value = true;
  error.value = null;
  try {
    prefs.value = await fetchPreferences();
  } catch (e) {
    error.value = e instanceof Error ? e.message : "Failed to load settings.";
  } finally {
    loading.value = false;
  }
}
onMounted(load);

async function change(changes: Partial<Preferences>) {
  const previous = { ...prefs.value };
  prefs.value = { ...prefs.value, ...changes };
  error.value = null;
  try {
    const { prefs: saved, latest } = await queuePreferences(changes);
    if (latest) {
      prefs.value = saved;
      sharedPreferences.value = saved;
    }
    savedNote.value = "Saved";
    setTimeout(() => (savedNote.value = ""), 1500);
  } catch (e) {
    error.value = e instanceof Error ? e.message : "Failed to save.";
    prefs.value = previous;
  }
}
function rule(kind: string): string {
  return prefs.value.library_relationship_display[kind] ?? "link";
}
function setRule(kind: string, value: string) {
  const rules = { ...prefs.value.library_relationship_display };
  if (value === "link") delete rules[kind];
  else rules[kind] = value as "nest" | "hidden";
  void change({ library_relationship_display: rules });
}
</script>

<template>
  <section class="settings-section" aria-labelledby="versions-settings">
    <h2 id="versions-settings">Game versions</h2>
    <p class="section-hint">
      Merged versions of one game (for example Minecraft Java and Bedrock) share
      one library entry. These choices only change what the library lists; every
      version, its playtime and its achievements are kept.
      <span v-if="savedNote" class="saved" role="status">{{ savedNote }}</span>
    </p>
    <p v-if="error" class="error" role="alert">
      {{ error }}
      <button
        type="button"
        class="ui-btn ui-btn-secondary ui-btn-sm"
        @click="load"
      >
        Retry
      </button>
    </p>
    <p v-if="loading" class="section-hint" role="status">Loading…</p>
    <template v-else>
      <div class="field">
        <span id="version-mode">Library entries</span>
        <SegmentedControl
          role="group"
          aria-labelledby="version-mode"
          :model-value="prefs.library_version_display"
          :options="modeOptions"
          @update:model-value="
            change({
              library_version_display:
                $event as Preferences['library_version_display'],
            })
          "
        />
        <small
          >A version can also be listed on its own from its game page, whatever
          you choose here.</small
        >
      </div>
      <fieldset class="rules">
        <legend>Related games (never merged)</legend>
        <p class="section-hint">
          How games linked by a relationship are listed. A single relationship
          can override this on its game page.
        </p>
        <label v-for="[kind, label] in KINDS" :key="kind" class="rule">
          <span>{{ label }}</span>
          <select
            class="ui-field"
            :value="rule(kind)"
            @change="setRule(kind, ($event.target as HTMLSelectElement).value)"
          >
            <option v-for="c in choices" :key="c.value" :value="c.value">
              {{ c.label }}
            </option>
          </select>
        </label>
      </fieldset>
    </template>
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
}
.field small {
  color: var(--ui-faint);
}
.rules {
  border: 1px solid var(--ui-border);
  border-radius: 12px;
  padding: 14px 16px;
  margin: 18px 0 0;
}
.rule {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 8px 0;
  font-size: 0.85rem;
}
@media (max-width: 600px) {
  .rule {
    flex-direction: column;
    align-items: stretch;
  }
}
</style>
