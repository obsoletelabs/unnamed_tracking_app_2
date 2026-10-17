<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import {
  fetchUmbrellaOverview,
  type UmbrellaOverview,
} from "../../services/gameIdentity";
import {
  agoLabel,
  dayLabel,
  hoursLabel,
  sourceName,
} from "../../utils/identityDisplay";
import GameIdentityDialog from "./GameIdentityDialog.vue";

const props = defineProps<{ gameId: string; title: string }>();
const emit = defineEmits<{ changed: [] }>();

const overview = ref<UmbrellaOverview | null>(null);
const loading = ref(true);
const error = ref<string | null>(null);
const managing = ref(false);
const showHidden = ref(false);
let request: AbortController | null = null;

async function load(): Promise<void> {
  request?.abort();
  request = new AbortController();
  const mine = request;
  loading.value = true;
  error.value = null;
  try {
    const found = await fetchUmbrellaOverview(props.gameId, mine.signal);
    if (!mine.signal.aborted) overview.value = found;
  } catch (cause) {
    if (mine.signal.aborted) return;
    error.value =
      cause instanceof Error ? cause.message : "Could not load the versions.";
  } finally {
    if (!mine.signal.aborted) loading.value = false;
  }
}

watch(() => props.gameId, load, { immediate: true });
onBeforeUnmount(() => request?.abort());

const many = computed(() => (overview.value?.versions.length ?? 0) > 1);
const shown = computed(
  () =>
    overview.value?.versions.filter(
      (v) => showHidden.value || v.display !== "hidden",
    ) ?? [],
);
const hiddenCount = computed(
  () =>
    overview.value?.versions.filter((v) => v.display === "hidden").length ?? 0,
);

function versionName(v: UmbrellaOverview["versions"][number]): string {
  if (v.label || v.edition) return (v.label || v.edition) as string;
  const main = overview.value?.versions.find((x) => x.is_primary);
  const own = v.title.trim().split(/\s+/);
  const base = (main?.title ?? "").trim().split(/\s+/);
  let shared = 0;
  while (
    shared < own.length &&
    shared < base.length &&
    own[shared].toLowerCase() === base[shared].toLowerCase()
  )
    shared++;
  const rest = own.slice(shared).join(" ");
  return shared > 0 && rest ? rest : v.title;
}
async function changed(): Promise<void> {
  await load();
  emit("changed");
}
defineExpose({ load });
</script>

<template>
  <section class="versions" aria-labelledby="versions-title">
    <header>
      <h2 id="versions-title">{{ many ? "Versions" : "Sources" }}</h2>
      <button
        type="button"
        class="ui-btn ui-btn-ghost"
        :aria-label="`Manage identity, versions and sources for ${title}`"
        @click="managing = true"
      >
        Manage identity
      </button>
    </header>
    <p v-if="loading && !overview" class="hint" role="status">Loading…</p>
    <div v-else-if="error" class="error" role="alert">
      <span>{{ error }}</span>
      <button type="button" class="ui-btn ui-btn-secondary" @click="load">
        Retry
      </button>
    </div>
    <template v-else-if="overview && many">
      <h3 class="group-title">Overall, across all versions</h3>
      <dl class="overall">
        <div>
          <dt>Playtime</dt>
          <dd data-testid="overall-playtime">
            {{ hoursLabel(overview.aggregate.playtime_seconds) }}
          </dd>
        </div>
        <div>
          <dt>Last played</dt>
          <dd>{{ dayLabel(overview.aggregate.last_played_at) }}</dd>
        </div>
        <div>
          <dt>Owned</dt>
          <dd>
            {{ overview.aggregate.owned_count }} of
            {{ overview.aggregate.version_count }}
            <span v-if="overview.aggregate.owned_platforms.length" class="sub">
              {{ overview.aggregate.owned_platforms.join(", ") }}
            </span>
          </dd>
        </div>
        <div>
          <dt>Completion</dt>
          <dd data-testid="overall-completion">
            {{ overview.aggregate.completion.summary }}
          </dd>
        </div>
        <div v-if="overview.aggregate.achievements.total">
          <dt>Achievements</dt>
          <dd>
            {{ overview.aggregate.achievements.unlocked }} of
            {{ overview.aggregate.achievements.total }}
            ({{ overview.aggregate.achievements.percent }}%)
          </dd>
        </div>
      </dl>
      <p v-if="!overview.aggregate.playtime_is_sum_of_versions" class="hint">
        Overall playtime counts a shared counter once, so it differs from the
        sum of the versions below.
      </p>
      <h3 class="group-title">Each version on its own</h3>
      <ul class="list">
        <li v-for="v in shown" :key="v.game_id" data-testid="version-row">
          <div class="name">
            <RouterLink
              v-if="v.game_id !== gameId"
              :to="`/games/${v.game_id}`"
              >{{ versionName(v) }}</RouterLink
            >
            <strong v-else>{{ versionName(v) }}</strong>
            <span v-if="v.is_primary" class="tag">Main</span>
            <span v-if="v.game_id === gameId" class="tag">This page</span>
            <span v-if="!v.owned" class="tag">Not owned</span>
            <span v-if="v.display === 'link'" class="tag"
              >Listed separately</span
            >
            <span v-if="v.display === 'hidden'" class="tag">Hidden</span>
          </div>
          <dl class="own">
            <div>
              <dt>Playtime</dt>
              <dd>{{ hoursLabel(v.playtime_seconds) }}</dd>
            </div>
            <div>
              <dt>Last played</dt>
              <dd>{{ dayLabel(v.last_played_at) }}</dd>
            </div>
            <div v-if="v.achievements.total">
              <dt>Achievements</dt>
              <dd>
                {{ v.achievements.unlocked }} / {{ v.achievements.total }}
              </dd>
            </div>
            <div>
              <dt>Completed</dt>
              <dd>{{ v.completed ? "Yes" : "No" }}</dd>
            </div>
          </dl>
          <p class="sub">
            {{ v.platform || "Unknown platform" }} ·
            <template v-if="v.sources.length">
              {{ v.sources.map(sourceName).join(", ") }}, updated
              {{ agoLabel(v.last_observed_at) }}
            </template>
            <template v-else>{{ sourceName(v.source) }}</template>
          </p>
        </li>
      </ul>
      <button
        v-if="hiddenCount"
        type="button"
        class="ui-btn ui-btn-ghost"
        :aria-pressed="showHidden"
        @click="showHidden = !showHidden"
      >
        {{ showHidden ? "Hide" : "Show" }} {{ hiddenCount }} hidden
        {{ hiddenCount === 1 ? "version" : "versions" }}
      </button>
    </template>
    <p v-else-if="overview" class="hint">
      One entry, no other versions. Open Manage identity to see where its values
      come from or to link another version.
    </p>
    <GameIdentityDialog
      v-if="managing"
      :game-id="gameId"
      :title="title"
      @close="managing = false"
      @changed="changed"
    />
  </section>
</template>

<style scoped>
.versions {
  padding: 20px;
  border: 1px solid var(--ui-border);
  border-radius: 12px;
  margin-bottom: 24px;
}
header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 12px;
}
h2 {
  margin: 0;
  font-size: 1.15rem;
}
.group-title {
  margin: 16px 0 0;
  font-size: 0.85rem;
  font-weight: 600;
  color: var(--ui-dim);
}
.hint,
.sub {
  color: var(--ui-dim);
  font-size: 0.85rem;
  margin: 8px 0 0;
}
.error {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  color: var(--ui-danger);
}
dl {
  margin: 0;
}
dt {
  color: var(--ui-dim);
  font-size: 0.78rem;
}
dd {
  margin: 2px 0 0;
  font-variant-numeric: tabular-nums;
}
.overall,
.own {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(130px, 1fr));
  gap: 12px 16px;
}
.overall {
  margin-top: 8px;
  padding: 14px;
  border-radius: 10px;
  background: var(--ui-accent-soft);
}
.list {
  list-style: none;
  padding: 0;
  margin: 4px 0 0;
}
.list li {
  padding: 14px 0;
  border-top: 1px solid var(--ui-border);
}
.name {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 8px;
  overflow-wrap: anywhere;
}
.name a {
  color: var(--ui-accent-text);
}
.tag {
  padding: 1px 7px;
  border: 1px solid var(--ui-border);
  border-radius: var(--ui-radius-control);
  color: var(--ui-dim);
  font-size: 0.72rem;
}
</style>
