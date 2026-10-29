<script setup lang="ts">
import {
  computed,
  onActivated,
  onBeforeUnmount,
  onDeactivated,
  onMounted,
  ref,
  watch,
} from "vue";
import { browserLoad, type BrowserLoad } from "../../utils/browserMetrics";
import { currentUser } from "../../state/auth";
import {
  fetchMetricsSnapshot,
  type MetricsSnapshot,
} from "../../services/metrics";
import {
  histogramQuantile,
  mergeHistograms,
  metricHistograms,
} from "../../utils/metrics";

const browser = ref<BrowserLoad | null>(null);
function captureBrowser() {
  browser.value = browserLoad(performance, location.origin);
}
function milliseconds(value: number | null) {
  return value === null ? "--" : `${value.toFixed(1)} ms`;
}
const history = ref<MetricsSnapshot[]>([]);
const error = ref<string | null>(null);
const loading = ref(false);
const paused = ref(false);
const route = ref("");
const method = ref("");
const family = ref("http");
const windowMode = ref("recent");
const active = ref(true);
let timer: ReturnType<typeof setTimeout> | undefined;
let controller: AbortController | undefined;
function visible() {
  return document.visibilityState !== "hidden";
}
const latest = computed(() => history.value.at(-1) ?? null);
const previous = computed(() =>
  windowMode.value === "recent" && history.value.length > 1
    ? history.value[0]!
    : null,
);
const elapsed = computed(() =>
  latest.value && previous.value
    ? Math.max(
        0,
        (latest.value.collected_at ?? 0) - (previous.value.collected_at ?? 0),
      )
    : 0,
);
const routes = computed(() =>
  [
    ...new Set(
      (latest.value?.histograms?.http ?? []).map((row) => row.labels.route!),
    ),
  ].sort(),
);
const methods = computed(() =>
  [
    ...new Set(
      (latest.value?.histograms?.http ?? []).map((row) => row.labels.method!),
    ),
  ].sort(),
);
const routeScoped = computed(
  () => family.value === "http" || family.value === "phases",
);
const http = computed(() =>
  mergeHistograms(
    metricHistograms(
      latest.value,
      previous.value,
      "http",
      route.value,
      method.value,
    ),
  ),
);
const rows = computed(() =>
  metricHistograms(
    latest.value,
    previous.value,
    family.value,
    routeScoped.value ? route.value : "",
    routeScoped.value ? method.value : "",
  )
    .filter((row) => row.count > 0)
    .sort(
      (a, b) =>
        (histogramQuantile(b, 0.95) ?? 0) - (histogramQuantile(a, 0.95) ?? 0),
    )
    .slice(0, 50),
);
const requestRate = computed(() =>
  elapsed.value ? http.value.count / elapsed.value : null,
);
function value(name: string): number | null {
  const values = latest.value?.values?.[name];
  return values?.length
    ? values.reduce((sum, item) => sum + item.value, 0)
    : null;
}
const memory = computed(() => value("memory_bytes"));
const cpu = computed(() => {
  const current = value("cpu_seconds");
  const before = previous.value?.values?.cpu_seconds?.reduce(
    (sum, item) => sum + item.value,
    0,
  );
  return current !== null &&
    before !== undefined &&
    elapsed.value &&
    current >= before
    ? ((current - before) / elapsed.value) * 100
    : null;
});
const points = computed(() =>
  history.value.map((snapshot, index) => {
    if (!index) return null;
    const previous = history.value[index - 1]!;
    const merged = mergeHistograms(
      metricHistograms(snapshot, previous, "http", route.value, method.value),
    );
    return histogramQuantile(merged, 0.95);
  }),
);
const chart = computed(() => {
  const maximum = Math.max(
    0.001,
    ...points.value.filter((point): point is number => point !== null),
  );
  return points.value
    .map((point, index) =>
      point === null
        ? ""
        : `${(index / Math.max(1, points.value.length - 1)) * 600},${100 - (point / maximum) * 90}`,
    )
    .filter(Boolean)
    .join(" ");
});
function ms(seconds: number | null): string {
  return seconds === null
    ? "--"
    : `${(seconds * 1000).toFixed(seconds < 0.01 ? 2 : 1)} ms`;
}
function describe(labels: Record<string, string>): string {
  return Object.entries(labels)
    .map(([key, value]) =>
      key === "route" || key === "method" ? value : `${key}: ${value}`,
    )
    .join("  /  ");
}
function stop() {
  clearTimeout(timer);
  controller?.abort();
}
async function refresh() {
  clearTimeout(timer);
  if (
    !active.value ||
    paused.value ||
    !visible() ||
    !currentUser.value?.is_admin ||
    loading.value
  )
    return;
  const request = new AbortController();
  controller = request;
  loading.value = true;
  try {
    const snapshot = await fetchMetricsSnapshot(request.signal);
    if (request.signal.aborted || !active.value || !currentUser.value?.is_admin)
      return;
    const last = latest.value;
    if (last?.started_at !== snapshot.started_at) history.value = [];
    if (!snapshot.enabled) history.value = [snapshot];
    else if (last?.collected_at !== snapshot.collected_at)
      history.value = [...history.value, snapshot]
        .filter(
          (item) =>
            (item.collected_at ?? 0) >= (snapshot.collected_at ?? 0) - 300,
        )
        .slice(-31);
    error.value = null;
  } catch (reason) {
    if (!request.signal.aborted)
      error.value =
        reason instanceof Error ? reason.message : "Could not load metrics.";
  } finally {
    loading.value = false;
    if (
      active.value &&
      !paused.value &&
      visible() &&
      latest.value?.enabled !== false
    )
      timer = setTimeout(() => void refresh(), 10_000);
  }
}
function visibility() {
  if (!visible()) stop();
  else void refresh();
}
watch(paused, (state) => {
  if (state) stop();
  else void refresh();
});
watch(
  () => currentUser.value?.id,
  () => {
    stop();
    history.value = [];
    void refresh();
  },
);
onMounted(() => {
  captureBrowser();
  document.addEventListener("visibilitychange", visibility);
  void refresh();
});
onActivated(() => {
  active.value = true;
  void refresh();
});
onDeactivated(() => {
  active.value = false;
  stop();
});
onBeforeUnmount(() => {
  active.value = false;
  stop();
  document.removeEventListener("visibilitychange", visibility);
});
</script>

<template>
  <section
    class="settings-section metrics-viewer"
    aria-labelledby="metrics-title"
  >
    <div class="metrics-heading">
      <div>
        <h2 id="metrics-title">Performance metrics</h2>
        <p class="hint">
          API response time, database work and plugin transport from the
          server's existing telemetry.
        </p>
      </div>
      <div class="metrics-actions">
        <button
          class="ui-btn ui-btn-ghost"
          :aria-pressed="paused"
          @click="paused = !paused"
        >
          {{ paused ? "Resume" : "Pause" }}</button
        ><button
          class="ui-btn ui-btn-secondary"
          :disabled="loading || paused"
          @click="refresh"
        >
          Refresh
        </button>
      </div>
    </div>
    <p v-if="error" class="ui-alert" role="alert">{{ error }}</p>
    <p v-if="!latest && loading" class="ui-state" role="status">
      Loading performance metrics...
    </p>
    <div v-else-if="latest && !latest.enabled" class="ui-state">
      <h3>Metrics are disabled</h3>
      <p>
        Enable METRICS_ENABLED on the server, then restart it to collect
        performance data.
      </p>
    </div>
    <details class="browser-load">
      <summary>Browser load timings</summary>
      <p class="hint">
        Local measurements for this browser's current document, with no
        telemetry uploads. Hard refresh the page you want to inspect, then open
        this viewer without refreshing again.
      </p>
      <button class="ui-btn ui-btn-secondary" @click="captureBrowser">
        Capture browser timings
      </button>
      <template v-if="browser">
        <p>
          Document received: {{ milliseconds(browser.responseEnd) }} / DOM
          ready: {{ milliseconds(browser.domReady) }} / Load:
          {{ milliseconds(browser.load) }}
        </p>
        <p v-if="browser.bootstrapGap !== null">
          Initial assets downloaded to bootstrap entry:
          {{ milliseconds(browser.bootstrapGap) }}. This can include module
          parsing, evaluation and browser scheduling.
        </p>
        <p v-for="mark in browser.marks" :key="mark.name">
          {{ mark.name }}: {{ milliseconds(mark.time) }} after navigation.
        </p>
        <p class="hint">
          Before send includes browser scheduling, cache/service-worker
          handling, DNS and connection setup. It is not an exact copy of
          DevTools Queued/Stalled. Waiting includes transit, proxy and server
          work. App is the authenticated response's server handling time. Cached
          or restricted timings can be unavailable.
        </p>
        <div class="metrics-table">
          <table>
            <thead>
              <tr>
                <th scope="col">Resource</th>
                <th scope="col">Protocol</th>
                <th scope="col">Starts at</th>
                <th scope="col">Before send</th>
                <th scope="col">Waiting</th>
                <th scope="col">App</th>
                <th scope="col">Download</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(resource, index) in browser.resources" :key="index">
                <th scope="row">{{ resource.path }}</th>
                <td>{{ resource.protocol }}</td>
                <td>{{ milliseconds(resource.start) }}</td>
                <td>{{ milliseconds(resource.beforeSend) }}</td>
                <td>{{ milliseconds(resource.waiting) }}</td>
                <td>{{ milliseconds(resource.server) }}</td>
                <td>{{ milliseconds(resource.download) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </template>
    </details>
    <template v-if="latest?.enabled">
      <div class="metrics-filters">
        <label
          >Route<select class="ui-field" v-model="route">
            <option value="">All API routes</option>
            <option v-for="item in routes" :key="item" :value="item">
              {{ item }}
            </option>
          </select></label
        >
        <label
          >Method<select class="ui-field" v-model="method">
            <option value="">All methods</option>
            <option v-for="item in methods" :key="item" :value="item">
              {{ item }}
            </option>
          </select></label
        >
        <label
          >Window<select class="ui-field" v-model="windowMode">
            <option value="recent">Observed last five minutes</option>
            <option value="total">Since server start</option>
          </select></label
        >
      </div>
      <p class="hint" role="status">
        {{
          previous
            ? `Observed ${elapsed.toFixed(0)} seconds while this viewer is open`
            : "Cumulative since server start; collecting the first interval"
        }}
        /
        {{
          latest.scope === "workers"
            ? "Aggregated workers"
            : "Current server process"
        }}
        / Updates every 10 seconds while visible.
      </p>
      <div class="metrics-cards">
        <article v-for="percentile in [0.5, 0.95, 0.99]" :key="percentile">
          <span>Response start p{{ percentile * 100 }}</span
          ><strong>{{ ms(histogramQuantile(http, percentile)) }}</strong>
        </article>
        <article>
          <span>Requests / second</span
          ><strong>{{
            requestRate === null ? "--" : requestRate.toFixed(1)
          }}</strong>
        </article>
        <article>
          <span>Process memory</span
          ><strong>{{
            memory === null
              ? "Unavailable"
              : `${(memory / 1024 / 1024).toFixed(0)} MiB`
          }}</strong>
        </article>
        <article>
          <span>CPU / one core = 100%</span
          ><strong>{{ cpu === null ? "--" : `${cpu.toFixed(1)}%` }}</strong>
        </article>
      </div>
      <figure class="metrics-chart" v-if="points.length > 1">
        <figcaption>
          Response-start p95 / each observed interval / selected route and
          method
        </figcaption>
        <svg
          viewBox="0 0 600 110"
          role="img"
          aria-label="Response-start p95 over time"
        >
          <polyline
            :points="chart"
            fill="none"
            stroke="currentColor"
            stroke-width="2"
            vector-effect="non-scaling-stroke"
          />
        </svg>
      </figure>
      <label class="metrics-family"
        >Breakdown<select class="ui-field" v-model="family">
          <option value="http">API routes</option>
          <option value="phases">Request phases</option>
          <option value="sql">SQL query families</option>
          <option value="pool_wait">Database pool acquisition</option>
          <option value="commits">Database commits</option>
          <option value="runtime">Plugin runtime calls</option>
          <option value="cache">Cache rebuilds</option>
        </select></label
      >
      <p class="hint">
        {{
          routeScoped
            ? "Route and method filters apply to this table."
            : "This table covers all routes; route and method filters apply to the response cards and chart."
        }}
        Slowest 50 groups by p95.
      </p>
      <div class="metrics-table">
        <table>
          <thead>
            <tr>
              <th scope="col">Operation</th>
              <th scope="col">Samples</th>
              <th scope="col">Mean</th>
              <th scope="col">p50</th>
              <th scope="col">p95</th>
              <th scope="col">p99</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in rows" :key="describe(row.labels)">
              <th scope="row">{{ describe(row.labels) }}</th>
              <td>{{ row.count.toLocaleString() }}</td>
              <td>{{ ms(row.sum / row.count) }}</td>
              <td>{{ ms(histogramQuantile(row, 0.5)) }}</td>
              <td>{{ ms(histogramQuantile(row, 0.95)) }}</td>
              <td>{{ ms(histogramQuantile(row, 0.99)) }}</td>
            </tr>
            <tr v-if="!rows.length">
              <td colspan="6">No samples in this window yet.</td>
            </tr>
          </tbody>
        </table>
      </div>
      <details class="metrics-notes">
        <summary>What these measurements include</summary>
        <p>
          Response start measures server handling until response headers.
          Browser queueing, network transit, JavaScript execution, image
          decoding and page paint are outside this timer. Percentiles are
          estimates from histogram buckets; sparse p99 samples are not reliable.
        </p>
        <p>
          Phases overlap and must not be added together. SQL measures cursor
          execution; pool acquisition and commits are separate, overlapping
          views. No SQL text, parameters, account identifiers or scrape
          credentials are shown.
        </p>
        <p>
          The recent window exists only while this viewer is open. Pausing keeps
          the displayed snapshot. Use Prometheus and the Grafana templates for
          durable history and comparisons across replicas.
        </p>
      </details>
    </template>
  </section>
</template>

<style scoped>
.browser-load {
  margin: 1rem 0;
  padding: 1rem;
  border: 1px solid var(--ui-border);
  border-radius: var(--ui-radius-card);
}
.metrics-heading,
.metrics-actions,
.metrics-filters {
  display: flex;
  gap: 1rem;
  align-items: center;
  flex-wrap: wrap;
}
.metrics-heading {
  justify-content: space-between;
  align-items: flex-start;
}
.metrics-filters {
  margin: 1rem 0;
}
.metrics-filters label,
.metrics-family {
  display: grid;
  gap: 0.4rem;
  min-width: 9rem;
}
.metrics-filters label:first-child {
  flex: 1;
  min-width: 12rem;
}
.metrics-filters select,
.metrics-family select {
  width: 100%;
  max-width: 100%;
}
.metrics-cards {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  gap: 0.75rem;
  margin: 1rem 0;
}
.metrics-cards article {
  border: 1px solid var(--ui-border);
  border-radius: var(--ui-radius-card);
  padding: 1rem;
  display: grid;
  gap: 0.6rem;
}
.metrics-cards span {
  font-size: 0.8rem;
  color: var(--ui-dim);
}
.metrics-cards strong {
  font-size: 1.35rem;
  font-variant-numeric: tabular-nums;
}
.metrics-chart {
  margin: 1rem 0;
  border: 1px solid var(--ui-border);
  border-radius: var(--ui-radius-card);
  padding: 1rem;
}
.metrics-chart figcaption {
  font-size: 0.8rem;
  color: var(--ui-dim);
}
.metrics-chart svg {
  display: block;
  width: 100%;
  max-height: 160px;
  color: var(--ui-accent);
}
.metrics-family {
  max-width: 24rem;
  margin-top: 1.5rem;
}
.metrics-table {
  overflow-x: auto;
  margin: 1rem 0;
}
.metrics-table table {
  border-collapse: collapse;
  width: 100%;
  font-size: 0.85rem;
}
.metrics-table th,
.metrics-table td {
  border-bottom: 1px solid var(--ui-border);
  text-align: left;
  padding: 0.75rem;
  font-variant-numeric: tabular-nums;
}
.metrics-table th:first-child {
  min-width: 15rem;
  overflow-wrap: anywhere;
}
.metrics-table td {
  white-space: nowrap;
}
.metrics-notes {
  margin-top: 1rem;
}
.metrics-notes p {
  line-height: 1.6;
}
</style>
