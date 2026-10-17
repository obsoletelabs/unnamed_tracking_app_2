<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import DiagnosticsTrace from "./DiagnosticsTrace.vue";
import {
  fetchOverview,
  previewDigest,
  previewRender,
  sendTest,
  simulate,
} from "../../services/notificationDiagnostics";
import type {
  DigestResult,
  Overview,
  PreviewResult,
  Simulation,
  TestSendResult,
} from "../../services/notificationDiagnostics";
import { fetchNotificationRouting } from "../../services/notifications";
import type { NotificationRoutingDestination } from "../../services/notifications";

const overview = ref<Overview | null>(null);
const destinations = ref<NotificationRoutingDestination[]>([]);
const loadError = ref<string | null>(null);

const fixture = ref("standard");
const failure = ref("");
const severity = ref("");
const simulationDestination = ref("");
const simulation = ref<Simulation | null>(null);

const variant = ref("rich");
const previewProvider = ref("");
const preview = ref<PreviewResult | null>(null);

const digestType = ref("game.sale.started");
const digestProvider = ref("core.smtp");
const digestCount = ref(20);
const digest = ref<DigestResult | null>(null);

const testDestination = ref("");
const testVariant = ref("basic");
const testBatch = ref(1);
const confirmed = ref(false);
const sent = ref<TestSendResult | null>(null);

const busy = ref<string | null>(null);
const error = ref<string | null>(null);

const externalDestinations = computed(() =>
  destinations.value.filter((item) => item.context === "external"),
);
const selectedFixture = computed(() =>
  overview.value?.fixtures.find((item) => item.id === fixture.value),
);

onMounted(async () => {
  try {
    const [loaded, routing] = await Promise.all([
      fetchOverview(),
      fetchNotificationRouting(),
    ]);
    overview.value = loaded;
    destinations.value = routing.destinations;
    testDestination.value = externalDestinations.value[0]?.id ?? "";
  } catch (e) {
    loadError.value = e instanceof Error ? e.message : "Failed to load.";
  }
});

async function run<T>(name: string, task: () => Promise<T>): Promise<T | null> {
  busy.value = name;
  error.value = null;
  try {
    return await task();
  } catch (e) {
    error.value = e instanceof Error ? e.message : "Request failed.";
    return null;
  } finally {
    busy.value = null;
  }
}

async function runSimulation() {
  simulation.value = await run("simulate", () =>
    simulate({
      fixture: fixture.value,
      destination_id: simulationDestination.value || undefined,
      failure: failure.value || undefined,
      severity: severity.value || undefined,
    }),
  );
}
async function runPreview() {
  preview.value = await run("preview", () =>
    previewRender({
      variant: variant.value,
      provider_id: previewProvider.value || undefined,
      fixture: fixture.value,
    }),
  );
}
async function runDigest() {
  digest.value = await run("digest", () =>
    previewDigest({
      event_type: digestType.value,
      provider_id: digestProvider.value,
      count: digestCount.value,
    }),
  );
}
async function runTest() {
  sent.value = await run("test", () =>
    sendTest({
      destination_id: testDestination.value,
      variant: testVariant.value,
      batch_size: testBatch.value,
    }),
  );
  if (sent.value) confirmed.value = false;
}
</script>

<template>
  <section class="diagnostics" aria-labelledby="diag-title">
    <h2 id="diag-title">Notification diagnostics</h2>
    <p class="hint">
      Dry runs evaluate fixtures against your own destinations and change
      nothing. Only “Send a test” queues work, through the real dispatcher.
      Credentials, recipient details and other people’s content are never shown.
    </p>
    <p v-if="loadError" class="form-error" role="alert">{{ loadError }}</p>
    <p v-if="error" class="form-error" role="alert">{{ error }}</p>

    <template v-if="overview">
      <h3>Simulate a scenario</h3>
      <div class="row">
        <label
          >Scenario
          <select v-model="fixture">
            <option v-for="f in overview.fixtures" :key="f.id" :value="f.id">
              {{ f.label }}
            </option>
          </select>
        </label>
        <label
          >Destination
          <select v-model="simulationDestination">
            <option value="">All of mine</option>
            <option v-for="d in destinations" :key="d.id" :value="d.id">
              {{ d.label || d.provider_name }}
            </option>
          </select>
        </label>
        <label
          >Provider outcome
          <select v-model="failure">
            <option value="">Scenario default</option>
            <option v-for="f in overview.failures" :key="f" :value="f">
              {{ f.replace("_", " ") }}
            </option>
          </select>
        </label>
        <label
          >Severity
          <select v-model="severity">
            <option value="">Scenario default</option>
            <option value="info">Info</option>
            <option value="warning">Warning</option>
            <option value="critical">Critical (urgent)</option>
          </select>
        </label>
      </div>
      <p v-if="selectedFixture" class="hint">
        {{ selectedFixture.description }}
      </p>
      <button
        type="button"
        class="primary-button"
        :disabled="busy === 'simulate'"
        @click="runSimulation"
      >
        {{ busy === "simulate" ? "Running…" : "Run dry run" }}
      </button>
      <div v-if="simulation" aria-live="polite">
        <p class="badge">Dry run — nothing was queued or sent</p>
        <DiagnosticsTrace heading="Event" :stages="simulation.stages" />
        <DiagnosticsTrace
          v-for="d in simulation.destinations"
          :key="d.destination_id"
          :heading="`${d.name} (${d.provider_id})`"
          :stages="d.stages"
        />
      </div>

      <h3>Preview rendering</h3>
      <div class="row">
        <label
          >Content
          <select v-model="variant">
            <option v-for="v in overview.variants" :key="v" :value="v">
              {{ v }}
            </option>
          </select>
        </label>
        <label
          >Provider
          <select v-model="previewProvider">
            <option value="">Every provider</option>
            <option
              v-for="p in overview.providers"
              :key="p.provider_id"
              :value="p.provider_id"
            >
              {{ p.name }}
            </option>
          </select>
        </label>
      </div>
      <button
        type="button"
        class="primary-button"
        :disabled="busy === 'preview'"
        @click="runPreview"
      >
        Preview
      </button>
      <div v-if="preview" aria-live="polite">
        <p class="badge">Dry run — rendered, not sent</p>
        <p v-if="preview.redacted" class="hint">{{ preview.message }}</p>
        <article
          v-for="p in preview.providers"
          :key="p.provider_id"
          class="card"
        >
          <h4>{{ p.provider_id }}</h4>
          <template v-if="p.title !== undefined">
            <p class="meta">Renderer: {{ p.renderer }}</p>
            <p class="title">{{ p.title }}</p>
            <pre class="body">{{ p.body }}</pre>
            <pre v-if="p.html" class="body" aria-label="Email HTML source">{{
              p.html
            }}</pre>
            <p v-if="p.link" class="meta">Link: {{ p.link }}</p>
            <ul v-if="p.actions?.length" class="plain">
              <li v-for="a in p.actions" :key="a.url">
                Button “{{ a.label }}” → {{ a.url }}
              </li>
            </ul>
            <p v-if="p.images?.length" class="meta">
              Images: {{ p.images.length }}
            </p>
            <ul v-if="p.fallbacks?.length" class="plain">
              <li v-for="f in p.fallbacks" :key="f.code">{{ f.label }}</li>
            </ul>
          </template>
          <p v-else class="meta">{{ p.label }}</p>
        </article>
      </div>

      <h3>Digest dry run</h3>
      <div class="row">
        <label
          >Event type
          <select v-model="digestType">
            <option>game.sale.started</option>
            <option>system.plugin.update</option>
            <option>media.episode.aired</option>
          </select>
        </label>
        <label
          >Provider
          <select v-model="digestProvider">
            <option
              v-for="p in overview.providers"
              :key="p.provider_id"
              :value="p.provider_id"
            >
              {{ p.name }}
            </option>
          </select>
        </label>
        <label
          >Events
          <input v-model.number="digestCount" type="number" min="1" max="200" />
        </label>
      </div>
      <button
        type="button"
        class="primary-button"
        :disabled="busy === 'digest'"
        @click="runDigest"
      >
        Compose digest
      </button>
      <div v-if="digest" aria-live="polite">
        <p class="badge">Dry run — nothing grouped for real</p>
        <p class="meta">
          {{ digest.reason?.label ?? digest.label }}
          <template v-if="digest.window_start">
            Window {{ new Date(digest.window_start * 1000).toLocaleString() }}
            –
            {{ new Date((digest.window_end ?? 0) * 1000).toLocaleString() }}.
          </template>
        </p>
        <article v-for="c in digest.chunks" :key="c.part" class="card">
          <h4>Message {{ c.part }} ({{ c.items }} items)</h4>
          <p class="title">{{ c.title }}</p>
          <pre class="body">{{ c.body }}</pre>
        </article>
      </div>

      <h3>Providers and health</h3>
      <p class="hint">
        Configuration status and declared capabilities. Delivery is at least
        once; up to {{ overview.retry_policy.max_attempts }} attempts with waits
        of {{ overview.retry_policy.backoff_seconds.join(", ") }} seconds.
      </p>
      <ul class="plain providers">
        <li v-for="p in overview.providers" :key="p.provider_id">
          <strong>{{ p.name }}</strong>
          <span class="code">{{ p.health.status }}</span>
          <span class="meta">{{ p.profile?.renderer }} renderer</span>
          <p class="meta">{{ p.health.label }} {{ p.health.action }}</p>
          <p v-if="Object.keys(p.last_24h.statuses).length" class="meta">
            Last 24 hours:
            {{
              Object.entries(p.last_24h.statuses)
                .map(([k, v]) => `${v} ${k.replace("_", " ")}`)
                .join(", ")
            }}
          </p>
        </li>
      </ul>

      <h3>Send a test</h3>
      <p class="hint">
        Sends a clearly labelled test to one of your own external destinations
        using the real dispatcher and provider. Recovery and verification
        messages cannot be test-sent.
      </p>
      <p v-if="!externalDestinations.length" class="hint">
        Add an external destination to send a test.
      </p>
      <template v-else>
        <div class="row">
          <label
            >Destination
            <select v-model="testDestination">
              <option
                v-for="d in externalDestinations"
                :key="d.id"
                :value="d.id"
              >
                {{ d.label || d.provider_name }}
              </option>
            </select>
          </label>
          <label
            >Content
            <select v-model="testVariant">
              <option v-for="v in overview.variants" :key="v" :value="v">
                {{ v }}
              </option>
            </select>
          </label>
          <label
            >Messages
            <input
              v-model.number="testBatch"
              type="number"
              min="1"
              :max="overview.max_test_batch"
            />
          </label>
        </div>
        <label class="check">
          <input v-model="confirmed" type="checkbox" />
          I understand this sends real test messages.
        </label>
        <button
          type="button"
          class="primary-button"
          :disabled="!confirmed || busy === 'test'"
          @click="runTest"
        >
          {{ busy === "test" ? "Queuing…" : "Send test" }}
        </button>
        <p v-if="sent" class="form-success" role="status">
          Queued {{ sent.queued }} test message(s). Watch Delivery history in
          Notifications settings for the outcome.
        </p>
      </template>
    </template>
  </section>
</template>

<style scoped>
.diagnostics h2 {
  margin: 0 0 12px;
  font: var(--ui-weight-heading) var(--ui-font-heading)/1.4
    var(--ui-font-family);
  color: var(--ui-text);
}
.diagnostics h3 {
  margin: 24px 0 8px;
  font-size: 0.9rem;
  color: var(--ui-text);
}
.hint,
.meta {
  color: var(--ui-faint);
  font-size: 0.82rem;
  line-height: 1.5;
  margin: 4px 0 10px;
}
.row {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  margin-bottom: 8px;
}
label {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 0.82rem;
  color: var(--ui-text);
}
select,
input[type="number"] {
  background: var(--ui-bg);
  border: 1px solid var(--ui-border-strong);
  border-radius: var(--ui-radius-control);
  color: var(--ui-text);
  padding: 8px 10px;
  font: inherit;
  max-width: 100%;
}
.check {
  flex-direction: row;
  align-items: center;
  margin: 8px 0;
}
.primary-button {
  background: var(--ui-accent);
  color: var(--ui-on-accent);
  border: none;
  border-radius: var(--ui-radius-control);
  padding: 9px 16px;
  font-weight: 600;
  cursor: pointer;
}
.primary-button:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}
.badge {
  display: inline-block;
  margin: 12px 0 0;
  padding: 2px 10px;
  border: 1px solid var(--ui-border-strong);
  border-radius: 999px;
  font-size: 0.76rem;
  color: var(--ui-accent-text);
}
.card {
  border: 1px solid var(--ui-border);
  border-radius: var(--ui-radius-control);
  padding: 8px 12px;
  margin-top: 8px;
}
.card h4 {
  margin: 0 0 4px;
  font-size: 0.84rem;
  color: var(--ui-text);
}
.title {
  margin: 4px 0;
  font-weight: 600;
  color: var(--ui-text);
}
.body {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  margin: 4px 0;
  font-size: 0.8rem;
  color: var(--ui-dim);
  max-height: 14rem;
  overflow: auto;
}
.plain {
  list-style: none;
  padding: 0;
  margin: 4px 0;
  font-size: 0.8rem;
  color: var(--ui-dim);
}
.providers li {
  margin-bottom: 10px;
}
.code {
  margin: 0 8px;
  font-family: var(--ui-font-mono, monospace);
  color: var(--ui-accent-text);
}
.form-error {
  color: var(--ui-error);
  font-size: 13px;
}
.form-success {
  color: var(--ui-good);
  font-size: 13px;
}
</style>
