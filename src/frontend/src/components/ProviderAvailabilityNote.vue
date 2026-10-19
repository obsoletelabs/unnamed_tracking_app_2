<script setup lang="ts">
// Says, next to a metadata search, why it cannot run and where to fix it.
// The link opens in a new tab so a half-filled form is never lost; coming back
// to this tab re-checks the setup.
import type { SearchBlock } from "../services/providerAvailability";

defineProps<{ block: SearchBlock | null; id: string }>();
</script>

<template>
  <p v-if="block" :id="id" class="provider-note" role="status">
    {{ block.message }} You can still fill in the details by hand.
    <a
      v-if="block.setup"
      :href="block.setup.href"
      target="_blank"
      rel="noopener"
      >Set it up in {{ block.setup.label }}</a
    >
    <span v-else>Ask an administrator to set it up.</span>
  </p>
</template>

<style scoped>
.provider-note {
  margin: 6px 0 0;
  padding: 8px 10px;
  border-radius: var(--ui-radius-control);
  background: var(--ui-warning-soft);
  color: var(--ui-text);
  font-size: 0.8rem;
  line-height: 1.5;
}
.provider-note a {
  color: var(--ui-accent-text);
  font-weight: 600;
  margin-left: 4px;
}
</style>
