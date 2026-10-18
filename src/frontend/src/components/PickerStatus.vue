<script setup lang="ts">
import type { PickerState } from "../composables/useMediaPicker";

// The one-line state under a title search: loading, nothing found, or a
// failed search with a retry, so an empty list never hides an error.
defineProps<{ state: PickerState; error: string | null }>();
defineEmits<{ retry: [] }>();
</script>

<template>
  <p v-if="state === 'too-short'" class="picker-status">
    Type at least 2 letters to search.
  </p>
  <p v-else-if="state === 'loading'" class="picker-status" role="status">
    Searching your library…
  </p>
  <p v-else-if="state === 'empty'" class="picker-status" role="status">
    No titles in your library match.
  </p>
  <p v-else-if="state === 'error'" class="picker-status ui-error" role="alert">
    {{ error ?? "Search failed." }}
    <button
      type="button"
      class="ui-btn ui-btn-secondary"
      @click="$emit('retry')"
    >
      Retry
    </button>
  </p>
</template>

<style scoped>
.picker-status {
  margin: 6px 0 0;
  font-size: 0.85rem;
  color: var(--ui-text-muted, inherit);
}
</style>
