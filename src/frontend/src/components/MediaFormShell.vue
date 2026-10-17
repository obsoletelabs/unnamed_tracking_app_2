<script setup lang="ts" generic="T extends string">
import { ref } from "vue";
import EditorShell from "./EditorShell.vue";

// The dialog around the Movie, TV and Anime editors, built on the shared
// EditorShell: a hero, direct tabs, one scrolling body and Delete / Look up
// metadata / Cancel / Save pinned below it. Each editor keeps its own fields
// and decides what saving and deleting do. The metadata lookup (the `lookup`
// slot) sits above the tabs, so it is reachable from every tab and a staged
// result is reviewed in place; nothing is saved until Save.
const props = defineProps<{
  // "Movie", "TV Show" or "Anime", for the title and the Add button
  noun: string;
  editing: boolean;
  saving: boolean;
  deleting: boolean;
  error: string | null;
  tabs: readonly T[];
  heading: string;
  subtitle?: string;
  cover?: string | null;
  // a lookup result is being reviewed; Save waits for the choice
  lookupPending?: boolean;
}>();
const tab = defineModel<T>("tab", { required: true });

const emit = defineEmits<{
  closed: [];
  save: [];
  delete: [];
  cover: [];
}>();

// New items open with the lookup ready; saved ones keep it behind the button.
const lookupOpen = ref(!props.editing);
const lookupId = `media-lookup-${Math.random().toString(36).slice(2, 8)}`;
</script>

<template>
  <EditorShell
    v-model:tab="tab"
    :title="(editing ? 'Edit ' : 'Add ') + noun"
    :heading="heading"
    :subtitle="subtitle"
    :cover="cover"
    cover-label="Show artwork"
    :tabs="tabs"
    :tabs-label="`${noun} details`"
    :dismissible="!saving && !deleting"
    class="media-editor"
    @close="emit('closed')"
    @submit="emit('save')"
    @cover="emit('cover')"
  >
    <template #lead>
      <div
        v-if="lookupOpen || lookupPending"
        :id="lookupId"
        class="media-lookup"
      >
        <slot name="lookup" />
      </div>
    </template>

    <slot />

    <template #status>
      <p v-if="error" class="ui-error-box" role="alert">{{ error }}</p>
    </template>

    <template #actions="{ formId }">
      <button
        v-if="editing"
        type="button"
        class="danger-button"
        :disabled="deleting || saving"
        @click="emit('delete')"
      >
        {{ deleting ? "Deleting…" : "Delete" }}
      </button>
      <button
        type="button"
        class="secondary-button"
        :aria-expanded="lookupOpen"
        :aria-controls="lookupId"
        @click="lookupOpen = !lookupOpen"
      >
        Look up metadata
      </button>
      <span class="modal-actions-spacer"></span>
      <button
        type="button"
        class="secondary-button"
        :disabled="saving"
        @click="emit('closed')"
      >
        Cancel
      </button>
      <button
        type="submit"
        :form="formId"
        class="primary-button"
        :disabled="saving || deleting || lookupPending"
      >
        {{ saving ? "Saving…" : editing ? "Save Changes" : "Add " + noun }}
      </button>
    </template>
  </EditorShell>
</template>

<style scoped src="../styles/shared/editorForm.css"></style>
<style scoped src="../styles/shared/editorActions.css"></style>
<style scoped>
.media-lookup {
  padding: 14px;
  border: 1px solid var(--ui-border);
  border-radius: var(--ui-radius-row);
  background: var(--ui-surface-2);
}
</style>
