<script setup lang="ts">
// "Sources": which connections report this game (Playnite, a store sync, a
// plugin), what each contributes and, where two sources report the same
// playtime, which one is preferred. The choice is the counters' own source
// priority, so the Advanced dialog's Counters tab always agrees (#339, #349).
// A "Metadata" row names the source of every metadata field, marks locks and
// preferences the value does not follow, and links the fields the sources
// disagree on; the per-field list lives beside the field itself.
import { computed, onBeforeUnmount, ref, watch } from "vue";
import {
  fetchIdentitySources,
  setCounterPriority,
  type IdentitySources,
} from "../../services/gameIdentity";
import {
  fetchFieldSources,
  type FieldSources,
} from "../../services/gameFieldSources";
import {
  FIELD_LABELS,
  fieldProvenance,
  metadataSummary,
} from "../../utils/fieldSources";
import { agoLabel } from "../../utils/identityDisplay";
import {
  hasContest,
  preferenceChanges,
  summarizeSources,
} from "../../utils/gameSources";

const props = defineProps<{ gameId: string }>();
const emit = defineEmits<{ changed: [] }>();

const data = ref<IdentitySources | null>(null);
const fieldSources = ref<FieldSources>({});
const loading = ref(true);
const error = ref<string | null>(null);
const actionError = ref<string | null>(null);
const busy = ref<string | null>(null);
let request: AbortController | null = null;

async function load(): Promise<void> {
  request?.abort();
  request = new AbortController();
  const mine = request;
  loading.value = true;
  error.value = null;
  try {
    const found = await fetchIdentitySources(props.gameId, mine.signal);
    if (!mine.signal.aborted) data.value = found;
  } catch (cause) {
    if (mine.signal.aborted) return;
    error.value =
      cause instanceof Error ? cause.message : "Could not load the sources.";
  } finally {
    if (!mine.signal.aborted) loading.value = false;
  }
  // the metadata row is optional: the playtime sources show without waiting for it
  try {
    const fields = await fetchFieldSources(props.gameId, mine.signal);
    if (!mine.signal.aborted) fieldSources.value = fields;
  } catch {
    if (!mine.signal.aborted) fieldSources.value = {};
  }
}

watch(() => props.gameId, load, { immediate: true });
onBeforeUnmount(() => request?.abort());

const sources = computed(() =>
  data.value ? summarizeSources(data.value) : [],
);
const contested = computed(() => hasContest(sources.value));
const metadata = computed(() => metadataSummary(fieldSources.value));
const provenance = computed(() => fieldProvenance(fieldSources.value));
const showMetadata = computed(
  () =>
    !!metadata.value.preferredLabel ||
    metadata.value.disagreeing.length > 0 ||
    provenance.value.length > 0,
);

// the per-field list sits beside the field: take the person to its label
function showField(field: string): void {
  const label = document.getElementById(`field-source-${field}`);
  label?.scrollIntoView({ block: "center" });
  label?.focus();
}

async function prefer(key: string): Promise<void> {
  if (!data.value) return;
  busy.value = key;
  actionError.value = null;
  try {
    for (const change of preferenceChanges(data.value, key))
      await setCounterPriority(
        change.counterId,
        change.observationId,
        change.priority,
      );
    await load();
    emit("changed");
  } catch (cause) {
    actionError.value =
      cause instanceof Error ? cause.message : "That change did not work.";
  } finally {
    busy.value = null;
  }
}
</script>

<template>
  <section
    v-if="error || sources.length || showMetadata || (loading && !data)"
    class="sources"
    aria-labelledby="game-sources-title"
  >
    <h2 id="game-sources-title">Sources</h2>
    <p v-if="loading && !data" class="sub" role="status">Loading sources…</p>
    <div v-else-if="error" class="state" role="alert">
      <span>{{ error }}</span>
      <button type="button" class="ui-btn ui-btn-secondary" @click="load">
        Retry
      </button>
    </div>
    <template v-else>
      <p class="sub">
        {{
          contested
            ? "Two sources report this game's playtime. The preferred one is used; the other is kept."
            : "Where this game's numbers come from. Each source keeps its own record."
        }}
      </p>
      <p v-if="actionError" class="state" role="alert">{{ actionError }}</p>
      <ul class="rows">
        <li v-for="source in sources" :key="source.key" class="source">
          <div class="head">
            <strong>{{ source.name }}</strong>
            <span v-if="source.disconnected" class="tag">Disconnected</span>
            <span v-if="source.preferred" class="tag preferred"
              >Preferred for playtime</span
            >
            <button
              v-if="source.canPrefer"
              type="button"
              class="ui-btn ui-btn-secondary prefer"
              :disabled="busy !== null"
              @click="prefer(source.key)"
            >
              {{ busy === source.key ? "Saving…" : "Prefer this source" }}
            </button>
          </div>
          <p class="sub">
            {{ source.copies.map((copy) => copy.name).join(", ") }}
          </p>
          <ul v-if="source.contributes.length" class="chips">
            <li v-for="item in source.contributes" :key="item">{{ item }}</li>
          </ul>
          <p v-if="source.lastObservedAt" class="sub">
            Updated {{ agoLabel(source.lastObservedAt) }}
          </p>
        </li>
        <li v-if="showMetadata" class="source metadata-row">
          <div class="head">
            <strong>Metadata</strong>
            <span v-if="metadata.preferredLabel" class="tag preferred"
              >Preferred: {{ metadata.preferredLabel }} ({{
                metadata.preferredFields
              }}
              {{ metadata.preferredFields === 1 ? "field" : "fields" }})</span
            >
          </div>
          <ul class="provenance" aria-label="Where each field comes from">
            <li
              v-for="row in provenance"
              :key="row.field"
              :class="{ warn: row.preferenceNotInEffect && !row.locked }"
            >
              <span class="field">{{ row.label }}</span>
              <span class="from">
                <svg
                  v-if="row.locked"
                  viewBox="0 0 24 24"
                  width="11"
                  height="11"
                  fill="none"
                  stroke="currentColor"
                  stroke-width="2.4"
                  stroke-linecap="round"
                  stroke-linejoin="round"
                  role="img"
                  aria-label="Locked"
                >
                  <rect x="5" y="11" width="14" height="10" rx="2" />
                  <path d="M8 11V7a4 4 0 0 1 8 0v4" />
                </svg>
                {{ row.source }}
              </span>
              <span
                v-if="row.preferenceNotInEffect && !row.locked"
                class="pref-note"
                >{{ row.preferred }} preferred, not shown</span
              >
            </li>
          </ul>
          <p v-if="metadata.disagreeing.length" class="sub">
            Sources disagree on
            <template v-for="(field, i) in metadata.disagreeing" :key="field"
              >{{ i ? ", " : ""
              }}<button type="button" class="link" @click="showField(field)">
                {{ FIELD_LABELS[field] }}
              </button></template
            >.
          </p>
        </li>
      </ul>
    </template>
  </section>
</template>

<style scoped>
.sources {
  margin: 0 0 16px;
}
h2 {
  font-size: 1.05rem;
  margin: 0 0 6px;
}
.sub {
  color: var(--ui-dim);
  font-size: 0.85rem;
  line-height: 1.5;
  margin: 0 0 6px;
}
.rows {
  display: grid;
  gap: 8px;
  list-style: none;
  margin: 0;
  padding: 0;
}
.source {
  border: 1px solid var(--ui-border);
  border-radius: 10px;
  padding: 10px 12px;
}
.head {
  align-items: center;
  display: flex;
  flex-wrap: wrap;
  gap: 6px 8px;
}
.prefer {
  margin-left: auto;
}
.tag {
  border: 1px solid var(--ui-border);
  border-radius: 999px;
  color: var(--ui-dim);
  font-size: 0.72rem;
  font-weight: 600;
  padding: 1px 8px;
}
.tag.preferred {
  background: var(--ui-accent-soft);
  border-color: var(--ui-accent-line);
  color: var(--ui-text);
}
.chips {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 6px;
  list-style: none;
  margin: 4px 0;
  padding: 0;
}
.chips li {
  background: var(--ui-surface-soft, transparent);
  border: 1px solid var(--ui-border-soft);
  border-radius: 6px;
  font-size: 0.8rem;
  padding: 1px 8px;
}
.provenance {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  list-style: none;
  margin: 6px 0;
  padding: 0;
}
.provenance li {
  align-items: baseline;
  border: 1px solid var(--ui-border-soft);
  border-radius: 8px;
  display: inline-flex;
  flex-wrap: wrap;
  font-size: 0.8rem;
  gap: 2px 6px;
  padding: 3px 8px;
}
.provenance .field {
  color: var(--ui-dim);
}
.provenance .from {
  align-items: center;
  display: inline-flex;
  font-weight: 600;
  gap: 3px;
}
.provenance li.warn {
  background: var(--ui-warning-soft);
  border-color: var(--ui-warning);
}
.provenance .pref-note {
  color: var(--ui-warning);
  font-size: 0.75rem;
}
.link {
  background: none;
  border: 0;
  color: var(--ui-accent, inherit);
  cursor: pointer;
  font: inherit;
  padding: 0;
  text-decoration: underline;
}
.state {
  align-items: center;
  color: var(--ui-danger);
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
</style>
