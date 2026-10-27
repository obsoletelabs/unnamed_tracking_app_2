<script setup lang="ts">
import GameArtwork from "./GameArtwork.vue";
defineProps<{
  src?: string;
  title: string;
  custom?: boolean;
  busy?: boolean;
  error?: string | null;
}>();
const emit = defineEmits<{ upload: [file: File]; remove: [] }>();
function selected(event: Event) {
  const input = event.target as HTMLInputElement;
  const file = input.files?.[0];
  if (file) emit("upload", file);
  input.value = "";
}
</script>

<template>
  <section class="cover-editor" aria-label="Collection cover">
    <GameArtwork
      class="cover-preview"
      :src="src ?? ''"
      :title="`${title} cover`"
      :placeholder-key="title"
      fill
    />
    <div class="cover-controls">
      <strong>Collection cover</strong>
      <p>
        Upload your own, or use the first four titles in collection order. A
        selected cover title appears first.
      </p>
      <label class="ui-btn ui-btn-secondary" :class="{ busy }">
        {{ busy ? "Saving cover…" : "Upload cover" }}
        <input
          type="file"
          accept="image/*"
          :disabled="busy"
          aria-label="Upload collection cover"
          @change="selected"
        />
      </label>
      <button
        v-if="custom"
        class="ui-btn ui-btn-secondary"
        type="button"
        :disabled="busy"
        @click="emit('remove')"
      >
        Use automatic cover
      </button>
      <p v-if="error" class="ui-error-box" role="alert">{{ error }}</p>
    </div>
  </section>
</template>

<style scoped>
.cover-editor {
  display: flex;
  gap: var(--ui-space-4);
  align-items: center;
  margin-block: var(--ui-space-4);
}
.cover-preview {
  width: 80px;
  height: 120px;
  flex: 0 0 80px;
  border-radius: var(--ui-radius-card);
  overflow: hidden;
}
.cover-controls {
  min-width: 0;
}
.cover-controls p {
  color: var(--ui-dim);
  font-size: var(--ui-font-small);
}
label {
  position: relative;
  display: inline-flex;
  overflow: hidden;
  cursor: pointer;
}
input {
  position: absolute;
  inset: 0;
  opacity: 0;
  width: 100%;
  cursor: pointer;
}
.busy {
  opacity: 0.6;
  cursor: wait;
}
button {
  margin-inline-start: var(--ui-space-2);
}
</style>
