<script setup lang="ts">
import { computed } from "vue";
import type { MetadataProviderStatus } from "../../services/metadata";
import {
  lastUsedLabel,
  providerAvailability,
} from "../../utils/metadataProviders";

const props = defineProps<{
  id: string;
  title: string;
  hint: string;
  providers: MetadataProviderStatus[];
  lastUsed: Record<string, number>;
  isAdmin: boolean;
  disabled?: boolean;
}>();
const emit = defineEmits<{ move: [providerId: string, direction: -1 | 1] }>();

const rows = computed(() =>
  props.providers.map((provider) => ({
    provider,
    availability: providerAvailability(provider, props.isAdmin),
  })),
);
</script>

<template>
  <div class="field-block">
    <span :id="`${id}-label`" class="field-label">{{ title }}</span>
    <p class="field-sublabel">{{ hint }}</p>
    <p v-if="!providers.length" class="field-sublabel" role="status">
      No provider for this media type is enabled for your account.
    </p>
    <ol v-else class="provider-list" :aria-labelledby="`${id}-label`">
      <li
        v-for="({ provider, availability }, index) in rows"
        :key="provider.provider_id"
        class="provider-item"
        :class="`is-${availability.tone}`"
      >
        <span class="provider-rank" aria-hidden="true">{{ index + 1 }}</span>
        <span class="provider-item-label">
          <span class="provider-name">
            <span class="provider-title">{{ provider.name }}</span>
            <small class="provider-origin">{{
              provider.included ? "Built in" : "Plugin"
            }}</small>
          </span>
          <span class="provider-status" :class="availability.tone">
            {{ availability.label }}
            <template v-if="availability.hint">
              · {{ availability.hint }}
            </template>
            <router-link
              v-if="availability.action"
              :to="availability.action.to"
              >{{ availability.action.label }}</router-link
            >
          </span>
          <small class="provider-last-used">{{
            lastUsedLabel(provider, lastUsed)
          }}</small>
        </span>
        <div class="provider-arrows">
          <button
            type="button"
            :aria-label="`Move ${provider.name} up in ${title}`"
            :disabled="disabled || index === 0"
            @click="emit('move', provider.provider_id, -1)"
          >
            ↑
          </button>
          <button
            type="button"
            :aria-label="`Move ${provider.name} down in ${title}`"
            :disabled="disabled || index === providers.length - 1"
            @click="emit('move', provider.provider_id, 1)"
          >
            ↓
          </button>
        </div>
      </li>
    </ol>
  </div>
</template>

<style scoped>
.field-block {
  margin-bottom: 0;
  min-width: 0;
}
.field-label {
  display: block;
  font-size: 0.85rem;
  color: var(--ui-text);
  margin-bottom: 4px;
}
.field-sublabel {
  color: var(--ui-faint);
  font-size: 0.76rem;
  line-height: 1.5;
  margin: 0 0 12px;
}
.provider-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.provider-item {
  display: flex;
  align-items: center;
  gap: 10px;
  background: var(--ui-bg);
  border: 1px solid var(--ui-border-strong);
  border-radius: var(--ui-radius-control);
  padding: 10px 12px;
  color: var(--ui-text);
  font-size: 0.85rem;
}
.provider-item.is-unavailable {
  border-style: dashed;
  background: transparent;
}
.provider-item.is-unavailable .provider-name {
  color: var(--ui-dim);
}
.provider-rank {
  flex: none;
  min-width: 1.4em;
  color: var(--ui-faint);
  font-variant-numeric: tabular-nums;
  text-align: right;
}
.provider-item-label {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.provider-name {
  font-weight: 600;
  overflow-wrap: anywhere;
}
.provider-origin {
  color: var(--ui-faint);
  font-size: 0.7rem;
  font-weight: 400;
  margin-left: 4px;
}
.provider-status {
  font-size: 0.72rem;
  line-height: 1.4;
}
.provider-status.ready {
  color: var(--ui-good);
}
.provider-status.warning {
  color: var(--ui-dim);
}
.provider-status.unavailable {
  color: var(--ui-faint);
}
.provider-status a {
  color: var(--ui-accent-text);
  margin-left: 6px;
}
.provider-last-used {
  color: var(--ui-faint);
  font-size: 0.7rem;
  font-weight: 400;
}
.provider-arrows {
  display: flex;
  flex: none;
  gap: 4px;
}
.provider-arrows button {
  background: color-mix(in srgb, var(--ui-text) 8%, transparent);
  border: none;
  border-radius: 6px;
  color: var(--ui-text);
  min-width: 32px;
  min-height: 32px;
  cursor: pointer;
}
.provider-arrows button:disabled {
  opacity: 0.3;
  cursor: not-allowed;
}
@media (max-width: 760px) {
  .provider-arrows button {
    min-width: 44px;
    min-height: 44px;
  }
}
</style>
