<script setup lang="ts">
import { RouterLink } from "vue-router";
import type { SearchAvailability } from "../utils/metadataSearchAvailability";

// The visible reason an action is unavailable, with the way to fix it. Pair it
// with aria-describedby on the control it explains: a disabled button alone
// does not tell keyboard or touch users why.
defineProps<{ id: string; availability: SearchAvailability }>();
const emit = defineEmits<{ navigate: [] }>();
</script>

<template>
  <p
    v-if="!availability.available"
    :id="id"
    class="availability-notice"
    role="status"
  >
    {{ availability.reason }}
    <RouterLink
      v-if="availability.action"
      :to="availability.action.to"
      @click="emit('navigate')"
      >{{ availability.action.label }}</RouterLink
    >
  </p>
</template>

<style scoped>
.availability-notice {
  margin: 0;
  font-size: 0.85rem;
  opacity: 0.85;
}
.availability-notice a {
  margin-left: 0.25rem;
  min-height: 44px;
  display: inline-flex;
  align-items: center;
}
</style>
