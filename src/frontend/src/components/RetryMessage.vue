<script setup lang="ts">
// A load failure with a way to try again, for places that must not look empty.
defineProps<{ message: string }>();
const emit = defineEmits<{ retry: [] }>();
</script>

<template>
  <span class="retry-message" role="alert">
    {{ message }}
    <button
      type="button"
      class="ui-btn ui-btn-secondary"
      @click="emit('retry')"
    >
      Try again
    </button>
  </span>
</template>

<style scoped>
.retry-message {
  align-items: center;
  display: inline-flex;
  flex-wrap: wrap;
  gap: 8px 12px;
}
</style>
