<script setup lang="ts">
import type { ClassificationFields } from "../utils/mediaEditorFields";

// Descriptive metadata shared by the three media editors: genres, countries,
// languages, age rating and features. A lookup can offer these; the media
// type adds its own fields through the default slot.
const fields = defineModel<ClassificationFields>({ required: true });
</script>

<template>
  <h3 class="editor-section">Classification</h3>
  <label class="field">
    <span>Genres (comma-separated)</span>
    <input v-model="fields.genresInput" type="text" />
  </label>
  <div class="editor-grid">
    <label class="field">
      <span>Countries (comma-separated)</span>
      <input v-model="fields.countriesInput" type="text" />
    </label>
    <label class="field">
      <span>Languages (comma-separated)</span>
      <input v-model="fields.languagesInput" type="text" />
    </label>
    <label class="field">
      <span>Age rating</span>
      <input
        v-model="fields.ageRating"
        type="text"
        maxlength="20"
        placeholder="PG-13, TV-MA…"
      />
    </label>
    <slot />
  </div>
  <label class="field">
    <span>Features (comma-separated)</span>
    <input v-model="fields.featuresInput" type="text" />
  </label>
</template>

<style scoped src="../styles/shared/editorForm.css"></style>
