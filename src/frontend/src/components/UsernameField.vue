<script setup lang="ts">
import { computed, ref, useId } from "vue";
import {
  USERNAME_HELP,
  USERNAME_MAX_LENGTH,
  usernameFieldError,
} from "../services/usernameValidation";

// One username input for every account form (profile, admin create, invitation).
// It explains the broken rule as soon as the person types or leaves the field.
// `serverError` carries the save response (for example a conflict) together with
// the text that was submitted; it is only shown while the field still holds that
// text, so neither an edit nor a slow reply can leave a stale message behind.
const props = withDefaults(
  defineProps<{
    modelValue: string;
    label?: string;
    help?: string;
    serverError?: { message: string; value: string } | null;
    unchangedValue?: string | null;
    required?: boolean;
    autocomplete?: string;
    showErrors?: boolean;
  }>(),
  {
    label: "Username",
    help: USERNAME_HELP,
    serverError: null,
    unchangedValue: null,
    required: false,
    autocomplete: "off",
    showErrors: false,
  },
);
const emit = defineEmits<{ "update:modelValue": [value: string] }>();

const uid = useId();
const inputId = `${uid}-input`;
const helpId = `${uid}-help`;
const errorId = `${uid}-error`;
const input = ref<HTMLInputElement | null>(null);
const interacted = ref(false);

const message = computed(() => {
  const server = props.serverError;
  if (server && server.value.trim() === props.modelValue.trim())
    return server.message;
  if (!interacted.value && !props.showErrors) return null;
  return usernameFieldError(props.modelValue, props.unchangedValue);
});

function onInput(event: Event) {
  interacted.value = true;
  emit("update:modelValue", (event.target as HTMLInputElement).value);
}

defineExpose({ focus: () => input.value?.focus() });
</script>

<template>
  <div class="field username-field">
    <label :for="inputId">{{ label }}</label>
    <input
      :id="inputId"
      ref="input"
      :value="modelValue"
      type="text"
      :maxlength="USERNAME_MAX_LENGTH * 2"
      :autocomplete="autocomplete"
      :required="required"
      :aria-invalid="message ? 'true' : undefined"
      :aria-describedby="message ? `${helpId} ${errorId}` : helpId"
      @input="onInput"
      @blur="interacted = true"
    />
    <small :id="helpId" class="username-help">{{ help }}</small>
    <small :id="errorId" class="username-error" aria-live="polite">{{
      message
    }}</small>
  </div>
</template>

<style scoped>
.username-field {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
  font-size: 0.85rem;
  color: var(--ui-text);
}
.username-field input {
  width: 100%;
  min-width: 0;
  box-sizing: border-box;
  background: var(--ui-bg);
  border: 1px solid var(--ui-border-strong);
  border-radius: var(--ui-radius-control);
  color: var(--ui-text);
  padding: 10px 12px;
  font: inherit;
}
.username-field input:focus {
  outline: none;
  border-color: var(--ui-accent);
}
.username-field input[aria-invalid="true"] {
  border-color: var(--ui-error);
}
.username-help {
  color: var(--ui-faint);
  font-size: 0.72rem;
}
.username-error {
  color: var(--ui-error);
  font-size: 0.8rem;
  font-weight: 600;
}
.username-error:empty {
  display: none;
}
</style>
