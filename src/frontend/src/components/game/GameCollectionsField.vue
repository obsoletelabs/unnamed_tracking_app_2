<script setup lang="ts">
// Manual collection memberships in the full game editor. Collections are
// names stored on the game, so this edits that list locally until Save.
// Names the field does not offer (such as one shadowed by a smart rule) are
// kept untouched; smart collections and Favorites fill themselves.
import { computed, ref, useId } from "vue";

const props = defineProps<{
  modelValue: string[];
  // manual collections that already exist, see manualCollectionNames()
  available: string[];
}>();
const emit = defineEmits<{ "update:modelValue": [names: string[]] }>();

const headingId = useId();
const newName = ref("");
// names typed into this editor that no other game uses yet
const added = ref(new Set<string>());
const choices = computed(() => {
  // a collection started in this editor stays offered after unchecking it
  const offered = new Set([...props.available, ...added.value]);
  return [...offered].sort((a, b) => a.localeCompare(b));
});
const member = computed(() => new Set(props.modelValue));

function toggle(name: string, checked: boolean) {
  const rest = props.modelValue.filter((item) => item !== name);
  emit("update:modelValue", checked ? [...rest, name] : rest);
}

function addNew() {
  const typed = newName.value.trim();
  if (!typed) return;
  // reuse an existing collection rather than creating a near-duplicate
  const existing =
    [...props.available, ...props.modelValue].find(
      (name) => name.toLowerCase() === typed.toLowerCase(),
    ) ?? typed;
  if (!props.available.includes(existing))
    added.value = new Set(added.value).add(existing);
  if (!member.value.has(existing))
    emit("update:modelValue", [...props.modelValue, existing]);
  newName.value = "";
}
</script>

<template>
  <fieldset class="collections-field" :aria-labelledby="headingId">
    <legend :id="headingId" class="collections-legend">Collections</legend>
    <div v-if="choices.length" class="collection-choices">
      <label
        v-for="name in choices"
        :key="name"
        class="collection-choice"
        :class="{ on: member.has(name) }"
      >
        <input
          type="checkbox"
          :checked="member.has(name)"
          @change="toggle(name, ($event.target as HTMLInputElement).checked)"
        />
        <span>{{ name }}</span>
      </label>
    </div>
    <p v-else class="hint">No collections yet. Add one below.</p>
    <div class="collection-new">
      <input
        v-model="newName"
        type="text"
        class="editor-input"
        maxlength="200"
        placeholder="New collection"
        aria-label="New collection name"
        @keydown.enter.prevent="addNew"
      />
      <button
        type="button"
        class="ui-btn ui-btn-secondary"
        :disabled="!newName.trim()"
        @click="addNew"
      >
        Add
      </button>
    </div>
    <p class="hint">
      Favorites and smart collections fill themselves from their rules. Changes
      are saved with the game.
    </p>
  </fieldset>
</template>

<style scoped src="../../styles/shared/editorForm.css"></style>
<style scoped>
.collections-field {
  display: flex;
  flex-direction: column;
  gap: 10px;
  min-width: 0;
  margin: 0;
  padding: 0;
  border: 0;
}
.collections-legend {
  margin-bottom: 4px;
  padding: 0;
  color: var(--ui-dim);
  font-size: var(--ui-font-small);
  font-weight: 600;
}
.collection-choices {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.collection-choice {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  min-height: 34px;
  padding: 0 12px;
  border: 1px solid var(--ui-border);
  border-radius: 999px;
  color: var(--ui-dim);
  font-size: 0.82rem;
  font-weight: 600;
  cursor: pointer;
}
.collection-choice input {
  width: 15px;
  height: 15px;
  margin: 0;
  accent-color: var(--ui-accent);
}
.collection-choice.on {
  border-color: var(--ui-accent-line);
  background: var(--ui-accent-soft);
  color: var(--ui-text);
}
.collection-choice:focus-within {
  outline: 2px solid var(--ui-accent);
  outline-offset: 2px;
}
.collection-new {
  display: flex;
  gap: 6px;
  max-width: 420px;
}
.collection-new .editor-input {
  min-width: 0;
}
</style>
