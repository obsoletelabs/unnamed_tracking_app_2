<script setup lang="ts">
import { reactive, watch } from "vue";
import PluginField from "../plugins/PluginField.vue";
import type { UiField, UiValue } from "../../services/pluginUi";

const props = defineProps<{
  fields: UiField[];
  editing?: boolean;
  busy?: boolean;
}>();
const emit = defineEmits<{ save: [values: Record<string, UiValue>] }>();
const values = reactive<Record<string, UiValue>>({});
watch(
  () => [props.fields, props.editing],
  () => {
    for (const key of Object.keys(values)) delete values[key];
    if (!props.editing) {
      for (const field of props.fields) {
        if (field.default != null) values[field.id] = field.default;
      }
    }
  },
  { immediate: true },
);
function save() {
  const submitted: Record<string, UiValue> = {};
  for (const field of props.fields) {
    const value = values[field.id];
    if (value === undefined || (props.editing && field.secret && value === ""))
      continue;
    submitted[field.id] = value;
  }
  emit("save", submitted);
}
</script>

<template>
  <form @submit.prevent="save">
    <p v-if="editing" class="hint">
      Saved values are never displayed. Leave a field untouched to retain it.
      Saving configuration cancels queued work and requires renewed media
      consent.
    </p>
    <PluginField
      v-for="field in fields"
      :key="field.id"
      v-model="values[field.id]"
      :field="editing ? { ...field, required: false } : field"
      :disabled="busy"
    />
    <slot />
    <button type="submit" :disabled="busy">
      {{ busy ? "Saving…" : "Save configuration" }}
    </button>
  </form>
</template>

<style scoped>
form {
  min-width: 0;
}
.hint {
  color: var(--ui-dim);
  font-size: 13px;
  line-height: 1.6;
}
button {
  background: var(--ui-surface);
  border: 1px solid var(--ui-border);
  border-radius: var(--ui-radius-control);
  color: var(--ui-text);
  padding: 8px 12px;
  cursor: pointer;
}
button:disabled {
  opacity: 0.5;
  cursor: default;
}
</style>
