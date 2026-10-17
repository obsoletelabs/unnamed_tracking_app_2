<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from "vue";
import {
  fetchReviewQueue,
  keepReviewEntrySeparate,
  mergeReviewEntry,
  runIdentityMatching,
  type ReviewEntry,
} from "../../services/identityReview";

const emit = defineEmits<{ (event: "ids", ids: string[]): void }>();
const entries = ref<ReviewEntry[]>([]);
const open = ref<string | null>(null);
const busy = ref<string | null>(null);
const error = ref<string | null>(null);
const loaded = ref(false);
const request = new AbortController();

const FIELD_LABELS: Record<string, string> = {
  title: "Title",
  source: "Source",
  platform: "Platform",
  release_date: "Release",
  playtime_seconds: "Playtime (min)",
  last_played_at: "Last played",
  external_id: "Import ID",
  provider_ids: "Provider IDs",
  status: "Status",
};

function show(field: string, value: unknown): string {
  if (value === null || value === undefined || value === "")
    return "Not recorded";
  if (field === "playtime_seconds")
    return String(Math.round(Number(value) / 60));
  if (field === "last_played_at")
    return new Date(Number(value) * 1000).toLocaleDateString();
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

async function load(): Promise<void> {
  try {
    const result = await fetchReviewQueue(request.signal);
    entries.value = result.entries;
    emit(
      "ids",
      result.entries.map((entry) => `${entry.first.id}:${entry.second.id}`),
    );
    loaded.value = true;
  } catch (cause) {
    if (!request.signal.aborted)
      error.value =
        cause instanceof Error ? cause.message : "Could not load the queue.";
  }
}

async function refresh(): Promise<void> {
  error.value = null;
  try {
    await runIdentityMatching();
  } catch (cause) {
    error.value =
      cause instanceof Error ? cause.message : "Could not check matches.";
  }
  await load();
}

async function resolve(
  entry: ReviewEntry,
  action: (id: string) => Promise<void>,
  failure: string,
): Promise<void> {
  busy.value = entry.id;
  error.value = null;
  try {
    await action(entry.id);
    entries.value = entries.value.filter((item) => item.id !== entry.id);
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : failure;
  } finally {
    busy.value = null;
  }
}

onMounted(refresh);
onBeforeUnmount(() => request.abort());
defineExpose({ refresh });
</script>

<template>
  <section class="queue" aria-labelledby="identity-queue-title">
    <h2 id="identity-queue-title">Review queue</h2>
    <p class="intro">
      Likely matches that were not certain enough to join automatically. Your
      choice is remembered, so the same pair is not suggested again.
    </p>
    <p v-if="error" class="queue-error" role="alert">{{ error }}</p>
    <p v-if="loaded && !entries.length" role="status">Nothing to review.</p>
    <article v-for="entry in entries" :key="entry.id" class="entry">
      <header>
        <strong>{{ entry.first.title }}</strong>
        <span v-if="entry.second.title !== entry.first.title">
          and {{ entry.second.title }}</span
        >
        <span class="confidence"
          >{{ Math.round(entry.confidence * 100) }}% likely</span
        >
      </header>
      <p class="intro">
        Matched on:
        {{
          (entry.evidence.matched || []).join(", ").replaceAll("_", " ") ||
          "title"
        }}.
        <template v-if="entry.evidence.missing?.length">
          Not confirmed:
          {{ entry.evidence.missing.join(", ").replaceAll("_", " ") }}.
        </template>
      </p>
      <table v-if="open === entry.id" class="differences">
        <caption class="sr-only">
          Differences
        </caption>
        <thead>
          <tr>
            <th scope="col">Field</th>
            <th scope="col">First</th>
            <th scope="col">Second</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="row in entry.differences"
            :key="row.field"
            :class="{ differs: !row.same }"
          >
            <th scope="row">{{ FIELD_LABELS[row.field] || row.field }}</th>
            <td>{{ show(row.field, row.first) }}</td>
            <td>{{ show(row.field, row.second) }}</td>
          </tr>
        </tbody>
      </table>
      <div class="entry-actions">
        <button
          class="ui-btn ui-btn-ghost"
          type="button"
          @click="open = open === entry.id ? null : entry.id"
        >
          {{ open === entry.id ? "Hide differences" : "Inspect differences" }}
        </button>
        <button
          class="ui-btn ui-btn-primary"
          type="button"
          :disabled="busy !== null"
          @click="resolve(entry, mergeReviewEntry, 'Could not merge.')"
        >
          Merge
        </button>
        <button
          class="ui-btn ui-btn-secondary"
          type="button"
          :disabled="busy !== null"
          @click="
            resolve(entry, keepReviewEntrySeparate, 'Could not keep separate.')
          "
        >
          Keep separate
        </button>
      </div>
    </article>
  </section>
</template>

<style scoped>
.queue {
  margin-bottom: 28px;
}
h2 {
  margin: 0 0 8px;
  font-size: 1.15rem;
}
.intro {
  color: var(--ui-dim);
  line-height: 1.5;
  margin: 0 0 12px;
}
.entry {
  border: 1px solid var(--ui-border);
  border-radius: 12px;
  margin-bottom: 12px;
  padding: 14px 18px;
}
.confidence {
  background: var(--ui-info-soft);
  border-radius: 999px;
  color: var(--ui-info);
  font-size: 0.75rem;
  margin-left: 8px;
  padding: 2px 8px;
}
.differences {
  border-collapse: collapse;
  font-size: 0.85rem;
  margin-bottom: 12px;
  width: 100%;
}
.differences th,
.differences td {
  border-bottom: 1px solid var(--ui-border-soft);
  overflow-wrap: anywhere;
  padding: 4px 8px;
  text-align: left;
}
.differs td {
  background: var(--ui-warning-soft);
}
.entry-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  justify-content: flex-end;
}
.queue-error {
  color: var(--ui-danger);
}
.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip: rect(0 0 0 0);
}
</style>
