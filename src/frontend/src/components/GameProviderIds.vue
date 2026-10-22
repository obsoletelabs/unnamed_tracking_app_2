<script setup lang="ts">
import { computed } from "vue";
import MediaProviderIds from "./MediaProviderIds.vue";
import {
  GAME_PROVIDER_ID_SUGGESTIONS,
  gameProviderLinks,
} from "../utils/gameProviderIds";

const ids = defineModel<Record<string, string>>({ required: true });
const emit = defineEmits<{ invalid: [message: string | null] }>();
const links = computed(() => gameProviderLinks(ids.value));
</script>

<template>
  <section
    class="provider-identities"
    aria-labelledby="provider-identities-heading"
  >
    <p id="provider-identities-heading" class="editor-section">
      Store and Atlas identities
    </p>
    <p class="hint">
      Atlas IDs identify canonical records. Store IDs connect matching entries
      from other sources; metadata refresh keeps saved IDs and adds new ones.
    </p>
    <MediaProviderIds
      v-model="ids"
      :suggestions="GAME_PROVIDER_ID_SUGGESTIONS"
      @invalid="emit('invalid', $event)"
    />
    <div v-if="links.length" class="store-links">
      <p class="hint">Known store pages</p>
      <ul>
        <li v-for="link in links" :key="link.namespace">
          <span>{{ link.label }} · {{ link.id }}</span>
          <a :href="link.url" target="_blank" rel="noopener noreferrer">
            Open store page
          </a>
        </li>
      </ul>
    </div>
  </section>
</template>

<style scoped src="../styles/shared/editorForm.css"></style>
<style scoped>
.provider-identities {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.store-links ul {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin: 4px 0 0;
  padding: 0;
  list-style: none;
}
.store-links li {
  display: flex;
  flex-wrap: wrap;
  justify-content: space-between;
  gap: 4px 12px;
}
</style>
