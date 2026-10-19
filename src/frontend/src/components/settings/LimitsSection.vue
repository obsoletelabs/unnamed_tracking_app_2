<script setup lang="ts">
import { ref, onMounted, useId } from "vue";
import {
  fetchUploadLimitSources,
  updateUploadLimits,
  type UploadLimits,
  type UploadLimitSources,
} from "../../services/settings";
const fieldId = useId();
const limits: { key: keyof UploadLimits; label: string; hint: string }[] = [
  {
    key: "max_upload_size_mb",
    label: "Images & general files",
    hint: "Cover art, banners, screenshots and other general uploads.",
  },
  {
    key: "max_doc_size_mb",
    label: "Documents",
    hint: "Each file added to a game's Docs tab, of any file type.",
  },
  {
    key: "max_save_archive_size_mb",
    label: "Save archives",
    hint: "Each version of a named game save archive.",
  },
  {
    key: "max_clip_size_mb",
    label: "Video clips",
    hint: "Clips and soundtracks uploaded to a game or the media inbox.",
  },
  {
    key: "max_world_save_size_mb",
    label: "World saves & modpacks",
    hint: "Each uploaded world save archive or modpack.",
  },
];
const loading = ref(true),
  saving = ref(false);
const error = ref("");
const success = ref("");
const effective = ref<UploadLimitSources | null>(null);
const drafts = ref<Partial<Record<keyof UploadLimits, string | number>>>({});
function apply(values: UploadLimitSources) {
  effective.value = values;
  for (const { key } of limits) drafts.value[key] = String(values[key].value);
}
const isLocked = (key: keyof UploadLimits) =>
  effective.value?.[key].locked ?? false;
function sourceNote(key: keyof UploadLimits): string {
  const info = effective.value?.[key];
  if (!info) return "";
  if (info.locked)
    return `Set by the server environment (${info.environment_variable}). Change it there and restart; it cannot be edited here.`;
  if (info.environment_invalid)
    return `${info.environment_variable} is set but is not a whole number of megabytes, so it is ignored.`;
  return info.source === "override"
    ? "Saved here, overriding the environment default."
    : "Using the built-in default.";
}
async function reload() {
  apply(await fetchUploadLimitSources());
}
onMounted(async () => {
  try {
    await reload();
  } catch (e) {
    error.value = e instanceof Error ? e.message : "Unable to load limits.";
  } finally {
    loading.value = false;
  }
});
async function save(reset = false) {
  error.value = "";
  success.value = "";
  const values: Partial<Record<keyof UploadLimits, number | null>> = {};
  for (const { key, label } of limits) {
    // an environment-controlled cap is not part of this form
    if (isLocked(key)) continue;
    const raw = String(drafts.value[key] ?? "").trim();
    const value = Number(raw);
    if (
      !reset &&
      (!raw || !Number.isInteger(value) || value < 1 || value > 2147483647)
    ) {
      error.value = `${label}: enter a whole number from 1 to 2147483647 MB.`;
      return;
    }
    values[key] = reset ? null : value;
  }
  saving.value = true;
  try {
    await updateUploadLimits(values);
    await reload();
    success.value = reset
      ? "Saved limits cleared. Environment-controlled limits are unchanged."
      : "Limits saved. New uploads use them immediately.";
  } catch (e) {
    error.value = e instanceof Error ? e.message : "Unable to save limits.";
  } finally {
    saving.value = false;
  }
}
</script>
<template>
  <section class="settings-section">
    <h2>Upload limits</h2>
    <p class="hint">
      The application's own limits in megabytes. A value set in the server's
      environment wins and is shown locked; otherwise what you save here
      applies, then the built-in default. A proxy in front of the application
      (Nginx <code class="inline">client_max_body_size</code>, a CDN) has its
      own request limit and can refuse a larger upload before the application
      sees it; multipart overhead adds a little to the file's size.
    </p>
    <p v-if="loading" class="hint">Loading…</p>
    <form v-else-if="effective" @submit.prevent="save()">
      <div v-for="limit in limits" :key="limit.key" class="field">
        <label :for="`${fieldId}-${limit.key}`">{{ limit.label }} (MB)</label>
        <input
          :id="`${fieldId}-${limit.key}`"
          :aria-describedby="`${fieldId}-${limit.key}-hint`"
          v-model="drafts[limit.key]"
          type="number"
          min="1"
          max="2147483647"
          step="1"
          inputmode="numeric"
          required
          :disabled="saving || isLocked(limit.key)"
        />
        <span :id="`${fieldId}-${limit.key}-hint`" class="field-hint"
          >{{ limit.hint }} {{ sourceNote(limit.key) }}
          <code>{{ limit.key.toUpperCase() }}</code></span
        >
      </div>
      <div class="row">
        <button type="submit" class="primary-button" :disabled="saving">
          {{ saving ? "Saving…" : "Save limits" }}
        </button>
        <button
          type="button"
          class="text-button"
          :disabled="saving"
          @click="save(true)"
        >
          Reset all to server defaults
        </button>
      </div>
    </form>
    <p v-if="error" class="form-error" role="alert">{{ error }}</p>
    <p v-if="success" class="form-success" role="status">{{ success }}</p>
  </section>
</template>
<style scoped>
.settings-section {
  max-width: 760px;
}
.settings-section h2 {
  margin: 0 0 12px;
  font: var(--ui-weight-heading) var(--ui-font-heading)/1.4
    var(--ui-font-family);
  color: var(--ui-text);
}
.hint {
  color: var(--ui-faint);
  font-size: 0.85rem;
  line-height: 1.5;
  margin: 0 0 18px;
}
.field {
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 0.85rem;
  color: var(--ui-text);
  margin-bottom: 12px;
}
.row {
  display: flex;
  gap: 10px;
}
.field input {
  flex: 1;
  min-width: 0;
  background: var(--ui-bg);
  border: 1px solid var(--ui-border-strong);
  border-radius: var(--ui-radius-control);
  color: var(--ui-text);
  padding: 10px 12px;
  font: inherit;
}
.field input:focus {
  border-color: var(--ui-accent);
}
.field-hint {
  color: var(--ui-faint);
  font-size: 0.78rem;
}
.primary-button {
  background: var(--ui-accent);
  color: var(--ui-on-accent);
  border: none;
  border-radius: var(--ui-radius-control);
  padding: 0 18px;
  font-weight: 600;
  cursor: pointer;
}
.primary-button:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}
.text-button {
  background: none;
  border: none;
  padding: 0;
  color: var(--ui-accent-text);
  font-size: 0.82rem;
  cursor: pointer;
}
.text-button:hover {
  text-decoration: underline;
}
.text-button:disabled {
  opacity: 0.6;
  cursor: not-allowed;
  text-decoration: none;
}
.form-error {
  margin-top: 12px;
  color: var(--ui-error);
  font-size: 13px;
  background: rgba(220, 38, 38, 0.1);
  border: 1px solid rgba(220, 38, 38, 0.3);
  border-radius: var(--ui-radius-control);
  padding: 8px 10px;
}
.form-success {
  margin-top: 12px;
  color: var(--ui-good);
  font-size: 13px;
  background: rgba(34, 197, 94, 0.1);
  border: 1px solid rgba(34, 197, 94, 0.3);
  border-radius: var(--ui-radius-control);
  padding: 8px 10px;
}
.row {
  flex-wrap: wrap;
}
button,
input {
  min-height: var(--ui-control-height);
}
code.inline {
  display: inline;
  margin: 0;
}
code {
  display: block;
  margin-top: 4px;
  overflow-wrap: anywhere;
}
</style>
