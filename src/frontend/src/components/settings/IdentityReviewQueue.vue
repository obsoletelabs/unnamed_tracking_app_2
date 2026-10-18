<script setup lang="ts">
import { onBeforeUnmount, onMounted, reactive, ref } from "vue";
import {
  fetchReviewQueue,
  keepReviewEntrySeparate,
  mergeReviewEntry,
  runIdentityMatching,
  type ReviewEntry,
  type ReviewGame,
} from "../../services/identityReview";
import { copyName } from "../../utils/gameCopies";
import { hoursLabel } from "../../utils/identityDisplay";

const emit = defineEmits<{ (event: "ids", ids: string[]): void }>();
const entries = ref<ReviewEntry[]>([]);
const open = ref<string | null>(null);
const busy = ref<string | null>(null);
const error = ref<string | null>(null);
const loaded = ref(false);
const request = new AbortController();
// which copy stays the main entry, per suggestion (default: the most played)
const mainChoice = reactive<Record<string, string>>({});

function mainOf(entry: ReviewEntry): string {
  return (
    mainChoice[entry.id] ??
    (entry.second.playtime_seconds > entry.first.playtime_seconds
      ? entry.second.id
      : entry.first.id)
  );
}
function headline(entry: ReviewEntry): string {
  const name = entry.first.title;
  return entry.kind === "copy"
    ? `Another copy of ${name}?`
    : `Possible duplicate: ${name}`;
}
function copyReason(entry: ReviewEntry): string {
  const differs = (entry.evidence.conflicts ?? []).filter((part) =>
    ["platform", "store", "edition"].includes(part),
  );
  if (!differs.length) return "Same game name.";
  const last = differs.pop()!;
  const list = differs.length ? `${differs.join(", ")} and ${last}` : last;
  return `Same game, on a different ${list}. Grouping keeps each copy's own saves and playtime.`;
}
function statusLabel(game: ReviewGame): string {
  const words = game.status.toLowerCase().replace(/_/g, " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

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
        <strong>{{ headline(entry) }}</strong>
        <span class="confidence"
          >{{ Math.round(entry.confidence * 100) }}% likely</span
        >
      </header>
      <fieldset class="pair">
        <legend class="sr-only">Choose the main copy</legend>
        <label
          v-for="game in [entry.first, entry.second]"
          :key="game.id"
          class="side"
          :class="{ chosen: mainOf(entry) === game.id }"
        >
          <span class="side-title">{{ game.title }}</span>
          <span class="side-copy">{{ copyName(game) }}</span>
          <span class="side-facts">
            {{ hoursLabel(game.playtime_seconds) }} ·
            {{ statusLabel(game) }}
          </span>
          <span class="side-main">
            <input
              type="radio"
              :name="`main-${entry.id}`"
              :value="game.id"
              :checked="mainOf(entry) === game.id"
              @change="mainChoice[entry.id] = game.id"
            />
            Main copy
          </span>
        </label>
      </fieldset>
      <p v-if="entry.kind === 'copy'" class="intro">
        {{ copyReason(entry) }}
      </p>
      <p v-else class="intro">
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
          @click="
            resolve(
              entry,
              (id) => mergeReviewEntry(id, mainOf(entry)),
              'Could not group these.',
            )
          "
        >
          {{ entry.kind === "copy" ? "Group as copies" : "Merge" }}
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
.pair {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 10px;
  border: 0;
  margin: 0 0 12px;
  padding: 0;
}
.side {
  display: grid;
  gap: 2px;
  padding: 10px 12px;
  border: 1px solid var(--ui-border);
  border-radius: 10px;
  cursor: pointer;
  min-width: 0;
}
.side.chosen {
  border-color: var(--ui-accent-line);
  background: var(--ui-accent-soft);
}
.side-title {
  font-weight: 600;
  overflow-wrap: anywhere;
}
.side-copy,
.side-facts {
  color: var(--ui-dim);
  font-size: 0.85rem;
}
.side-main {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 6px;
  font-size: 0.85rem;
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
