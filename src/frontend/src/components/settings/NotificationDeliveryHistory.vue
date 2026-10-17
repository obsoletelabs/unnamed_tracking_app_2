<script setup lang="ts">
import { onMounted, ref } from "vue";
import {
  fetchDeliveries,
  retryDelivery,
} from "../../services/notificationDiagnostics";
import type { DeliveryRecord } from "../../services/notificationDiagnostics";

const deliveries = ref<DeliveryRecord[]>([]);
const loading = ref(true);
const error = ref<string | null>(null);
const note = ref<string | null>(null);
const busy = ref<string | null>(null);

async function load() {
  loading.value = true;
  error.value = null;
  try {
    deliveries.value = (await fetchDeliveries()).deliveries;
  } catch (e) {
    error.value = e instanceof Error ? e.message : "Failed to load.";
  } finally {
    loading.value = false;
  }
}

async function retry(item: DeliveryRecord) {
  busy.value = item.id;
  note.value = null;
  error.value = null;
  try {
    await retryDelivery(item.id);
    note.value = "Queued for another attempt.";
    await load();
  } catch (e) {
    error.value = e instanceof Error ? e.message : "Could not retry.";
  } finally {
    busy.value = null;
  }
}

function when(timestamp: number | null): string {
  return timestamp ? new Date(timestamp * 1000).toLocaleString() : "";
}
onMounted(load);
</script>

<template>
  <div class="history">
    <h3>Delivery history</h3>
    <p class="hint">
      Recent deliveries to your external destinations: what happened, why, and
      what to do next. Message text, addresses and credentials are never shown.
    </p>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <p v-if="note" class="note" role="status">{{ note }}</p>
    <p v-if="loading" class="hint" role="status">Loading…</p>
    <p v-else-if="!deliveries.length" class="hint">No deliveries yet.</p>
    <ul v-else>
      <li v-for="item in deliveries" :key="item.id">
        <div class="head">
          <strong>{{ item.destination ?? item.provider_id }}</strong>
          <span class="status" :data-status="item.status">{{
            item.status.replace("_", " ")
          }}</span>
          <span v-if="item.test" class="tag">Test</span>
        </div>
        <p class="meta">
          {{ item.event_type }} · attempt {{ item.attempts }} of
          {{ item.max_attempts }}
          <template v-if="item.next_attempt_at">
            · next try {{ when(item.next_attempt_at) }}</template
          >
        </p>
        <p v-if="item.reason" class="reason">
          {{ item.reason.label }}
          <span class="next">{{ item.reason.action }}</span>
        </p>
        <ul v-if="item.fallbacks.length" class="fallbacks">
          <li v-for="fallback in item.fallbacks" :key="fallback.code">
            {{ fallback.label }}
          </li>
        </ul>
        <details v-if="item.attempt_log.length">
          <summary>{{ item.attempt_log.length }} attempt(s)</summary>
          <ul>
            <li v-for="attempt in item.attempt_log" :key="attempt.number">
              #{{ attempt.number }} {{ attempt.operation }}: {{ attempt.outcome
              }}<template v-if="attempt.reason">
                — {{ attempt.reason.label }}</template
              >
            </li>
          </ul>
        </details>
        <button
          v-if="item.retry_allowed"
          type="button"
          class="link"
          :disabled="busy === item.id"
          :aria-label="`Retry delivery to ${item.destination ?? item.provider_id}`"
          @click="retry(item)"
        >
          Retry now
        </button>
      </li>
    </ul>
    <button type="button" class="link" @click="load">Refresh</button>
  </div>
</template>

<style scoped>
h3 {
  margin: 22px 0 8px;
  font-size: 0.86rem;
  color: var(--ui-text);
}
.hint {
  color: var(--ui-dim);
  font-size: 0.82rem;
  line-height: 1.6;
  margin: 0 0 12px;
}
ul {
  list-style: none;
  padding: 0;
  margin: 0 0 10px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.history > ul > li {
  border: 1px solid var(--ui-border);
  border-radius: var(--ui-radius-control);
  padding: 10px 12px;
  font-size: 0.82rem;
  color: var(--ui-text);
}
.head {
  display: flex;
  gap: 8px;
  align-items: center;
  flex-wrap: wrap;
}
.status {
  font-size: 0.74rem;
  text-transform: capitalize;
  color: var(--ui-dim);
}
.status[data-status="sent"] {
  color: var(--ui-good);
}
.status[data-status="failed_permanent"] {
  color: var(--ui-error);
}
.tag {
  font-size: 0.7rem;
  border: 1px solid var(--ui-border-strong);
  border-radius: 999px;
  padding: 0 6px;
}
.meta,
.reason {
  margin: 4px 0 0;
  color: var(--ui-dim);
  line-height: 1.5;
}
.next {
  display: block;
  color: var(--ui-faint);
}
.fallbacks {
  margin: 4px 0;
  color: var(--ui-faint);
}
.error {
  color: var(--ui-error);
  font-size: 0.82rem;
}
.note {
  color: var(--ui-good);
  font-size: 0.82rem;
}
.link {
  background: none;
  border: none;
  padding: 0;
  color: var(--ui-accent-text);
  font-size: 0.82rem;
  cursor: pointer;
}
.link:hover {
  text-decoration: underline;
}
</style>
