<script setup lang="ts">
// The poster and backdrop of a movie, show or anime. Artwork chosen in a
// metadata lookup lands here only after the lookup is confirmed.
defineProps<{ noun: string }>();
const poster = defineModel<string | null>("poster", { required: true });
const backdrop = defineModel<string | null>("backdrop", { required: true });
</script>

<template>
  <div class="artwork-grid">
    <div class="artwork-slot">
      <h3 class="editor-section">Poster</h3>
      <img v-if="poster" :src="poster" :alt="`Poster of this ${noun}`" />
      <p v-else class="hint">No poster yet.</p>
      <label class="field">
        <span>Poster image URL</span>
        <input
          :value="poster ?? ''"
          type="url"
          placeholder="https://…"
          @input="poster = ($event.target as HTMLInputElement).value || null"
        />
      </label>
    </div>
    <div class="artwork-slot">
      <h3 class="editor-section">Backdrop</h3>
      <img
        v-if="backdrop"
        class="backdrop"
        :src="backdrop"
        :alt="`Backdrop of this ${noun}`"
      />
      <p v-else class="hint">No backdrop yet.</p>
      <label class="field">
        <span>Backdrop image URL</span>
        <input
          :value="backdrop ?? ''"
          type="url"
          placeholder="https://…"
          @input="backdrop = ($event.target as HTMLInputElement).value || null"
        />
      </label>
    </div>
  </div>
</template>

<style scoped src="../styles/shared/editorForm.css"></style>
<style scoped>
.artwork-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 2fr);
  gap: 16px;
}
.artwork-slot {
  display: flex;
  flex-direction: column;
  gap: 10px;
  min-width: 0;
}
.artwork-slot .editor-section {
  margin: 0;
}
.artwork-slot img {
  width: 100%;
  max-height: 260px;
  object-fit: cover;
  border: 1px solid var(--ui-border);
  border-radius: var(--ui-radius-control);
}
.artwork-slot img.backdrop {
  max-height: 180px;
}
@media (max-width: 720px) {
  .artwork-grid {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
