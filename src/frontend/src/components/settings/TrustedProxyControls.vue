<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { fetchProxyPresets, validateProxyEntries } from "../../services/realIp";
import type { ProxyPreset } from "../../services/realIp";

const props = defineProps<{
  modelValue: string;
  disabled?: boolean;
}>();

const emit = defineEmits<{
  "update:modelValue": [value: string];
}>();

const presets = ref<Record<string, ProxyPreset>>({});
const custom = ref("");
const error = ref<string | null>(null);
const busy = ref(false);
const entries = computed(() => tokens(props.modelValue));

function tokens(value: string): string[] {
  return value
    .split(/\s+/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function add(values: string[]) {
  if (props.disabled || busy.value) return;
  const merged = [...tokens(props.modelValue)];
  for (const value of values) {
    if (!merged.includes(value)) merged.push(value);
  }
  emit("update:modelValue", merged.join(" "));
}

function remove(value: string) {
  if (props.disabled || busy.value) return;
  emit(
    "update:modelValue",
    entries.value.filter((item) => item !== value).join(" "),
  );
}

async function addCustom() {
  if (props.disabled || busy.value) return;
  error.value = null;
  const values = tokens(custom.value);
  if (!values.length) return;
  busy.value = true;
  try {
    const normalized = await validateProxyEntries(
      [...entries.value, ...values].join(" "),
    );
    emit("update:modelValue", normalized);
    custom.value = "";
  } catch (reason) {
    error.value =
      reason instanceof Error
        ? reason.message
        : "Enter valid IP addresses or CIDR ranges.";
  } finally {
    busy.value = false;
  }
}

onMounted(async () => {
  try {
    presets.value = await fetchProxyPresets();
  } catch {
    error.value = "Unable to load proxy presets.";
  }
});
</script>

<template>
  <div class="proxy-controls">
    <div class="preset-buttons">
      <button
        v-for="(preset, key) in presets"
        :key="key"
        type="button"
        :disabled="disabled || busy"
        @click="add(preset.values)"
      >
        Enable {{ preset.label }}
      </button>
    </div>
    <ul class="proxy-list" aria-label="Trusted proxy ranges">
      <li v-for="entry in entries" :key="entry">
        <code>{{ entry }}</code
        ><button
          type="button"
          :aria-label="`Remove ${entry}`"
          :disabled="disabled || busy"
          @click="remove(entry)"
        >
          Remove
        </button>
      </li>
    </ul>
    <p v-if="!entries.length" class="empty">No proxy addresses are trusted.</p>
    <div class="custom-row">
      <textarea
        v-model="custom"
        :disabled="disabled || busy"
        rows="3"
        maxlength="8192"
        aria-label="Custom IP or CIDR ranges"
        placeholder="One IP/CIDR per line, e.g. 192.168.1.0/24"
      />
      <button
        type="button"
        :disabled="disabled || busy || !custom.trim()"
        @click="addCustom"
      >
        {{ busy ? "Validating…" : "Add custom" }}
      </button>
    </div>
    <small>
      Loopback is the safe default. Presets add trusted proxy ranges;
      environment-managed values are locked.
    </small>
    <small v-if="error" class="error" role="alert">{{ error }}</small>
  </div>
</template>

<style scoped>
.proxy-controls {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.preset-buttons {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.proxy-controls textarea {
  background: var(--ui-surface);
  border: 1px solid var(--ui-border-strong);
  border-radius: var(--ui-radius-control);
  color: var(--ui-text);
  padding: 10px;
  font: inherit;
}

.custom-row {
  display: flex;
  gap: 8px;
}

.custom-row textarea {
  flex: 1;
  min-width: 0;
  resize: vertical;
}

.proxy-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
  max-height: 360px;
  overflow: auto;
}
.proxy-list li {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  border: 1px solid var(--ui-border);
  border-radius: var(--ui-radius-control);
  padding: 6px 10px;
}
.proxy-list code {
  overflow-wrap: anywhere;
  min-width: 0;
}
.empty {
  color: var(--ui-dim);
  font-size: 13px;
}
@media (max-width: 760px) {
  .custom-row {
    flex-direction: column;
  }
}

.proxy-controls button {
  background: var(--ui-surface-2);
  color: var(--ui-text);
  border: 1px solid var(--ui-border-strong);
  border-radius: var(--ui-radius-control);
  padding: 8px 10px;
  cursor: pointer;
}

.proxy-controls small {
  color: var(--ui-faint);
  font-size: 11px;
}

.proxy-controls .error {
  color: var(--ui-error);
}
</style>
