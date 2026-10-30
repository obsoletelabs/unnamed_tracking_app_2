<script setup lang="ts">
// "Sources": which connections report this game (Playnite, a store sync, a
// plugin), what each contributes and, where two sources report the same
// playtime, which one is preferred. The choice is the counters' own source
// priority, so the Advanced dialog's Counters tab always agrees (#339, #349).
// A "Metadata" row shows the preferred metadata source and which fields the
// sources disagree on; the per-field list lives beside the field itself.
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
import { FIELD_LABELS, metadataSummary } from "../../utils/fieldSources";
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
const showMetadata = computed(
  () =>
    !!metadata.value.preferredLabel || metadata.value.disagreeing.length > 0,
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
