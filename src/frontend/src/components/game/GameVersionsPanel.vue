<script setup lang="ts">
// "Copies & versions": one game, and every copy of it you own or track (a
// Steam copy, a Switch copy, an edition...). Each copy keeps its own saves,
// playtime and imports; the overall numbers come from the backend's
// reconciliation, never summed here (#101, #274). Rare identity tools
// (sources, decisions, counters) stay in the Advanced dialog.
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { useRouter } from "vue-router";
import {
  fetchUmbrellaOverview,
  makeMainCopy,
  setRelationshipDisplay,
  splitCopy,
  type UmbrellaOverview,
  type VersionOverview,
} from "../../services/gameIdentity";
import { useConfirm } from "../../state/dialog";
import { copyName, storeName } from "../../utils/gameCopies";
import {
  agoLabel,
  dayLabel,
  hoursLabel,
  sourceName,
} from "../../utils/identityDisplay";
import GameAddCopyDialog from "./GameAddCopyDialog.vue";
import GameIdentityDialog from "./GameIdentityDialog.vue";

const props = defineProps<{ gameId: string; title: string }>();
const emit = defineEmits<{
  changed: [];
  /** The playtime this page stands for, so the page's other figures agree. */
  playtime: [seconds: number];
}>();
const confirm = useConfirm();
const router = useRouter();

const overview = ref<UmbrellaOverview | null>(null);
const loading = ref(true);
const error = ref<string | null>(null);
const actionError = ref<string | null>(null);
const busy = ref<string | null>(null); // game id of the copy being changed
const managing = ref(false);
const adding = ref(false);
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
    if (mine.signal.aborted) return;
    overview.value = found;
    const own = found.versions.find((v) => v.game_id === props.gameId);
    const isMain = own?.is_primary ?? true;
    emit(
      "playtime",
      isMain
        ? found.aggregate.playtime_seconds
        : (own?.playtime_seconds ?? found.aggregate.playtime_seconds),
    );
  } catch (cause) {
    if (mine.signal.aborted) return;
    error.value =
      cause instanceof Error ? cause.message : "Could not load the copies.";
  } finally {
    if (!mine.signal.aborted) loading.value = false;
  }
}

watch(() => props.gameId, load, { immediate: true });
onBeforeUnmount(() => request?.abort());

const versions = computed(() => overview.value?.versions ?? []);
const many = computed(() => versions.value.length > 1);
const main = computed(() => versions.value.find((v) => v.is_primary) ?? null);
const onMainPage = computed(() => main.value?.game_id === props.gameId);
const shown = computed(() =>
  versions.value.filter((v) => showHidden.value || v.display !== "hidden"),
);
const hiddenCount = computed(
  () => versions.value.filter((v) => v.display === "hidden").length,
);
const memberIds = computed(() => versions.value.map((v) => v.game_id));

/** "Steam · PC (Windows)", plus the edition when the titles differ. */
function copyTitle(v: VersionOverview): string {
  const where = copyName(v);
  // a label that only repeats the store or platform adds nothing
  const known = [v.platform, v.source, storeName(v.source)]
    .filter(Boolean)
    .map((part) => part!.toLowerCase());
  const edition = [v.label, v.edition, editionWords(v)].find(
    (part) => part && !known.includes(part.toLowerCase()),
  );
  return edition ? `${edition} · ${where}` : where;
}
function editionWords(v: VersionOverview): string | null {
  const base = (main.value?.title ?? props.title).trim().split(/\s+/);
  const own = v.title.trim().split(/\s+/);
  let shared = 0;
  while (
    shared < own.length &&
    shared < base.length &&
    own[shared].toLowerCase() === base[shared].toLowerCase()
  )
    shared++;
  if (shared === own.length && shared === base.length) return null;
  const rest = own
    .slice(shared)
    .join(" ")
    .replace(/^[:–—-]+\s*/, "");
  return shared > 0 && rest ? rest : v.title;
}

/** Import sources, leaving out entries added by hand. */
function syncedFrom(v: VersionOverview): string[] {
  return v.sources
    .filter((source) => source.toLowerCase() !== "manual")
    .map(sourceName);
}

async function act(v: VersionOverview, change: () => Promise<void>) {
  busy.value = v.game_id;
  actionError.value = null;
  try {
    await change();
    await load();
    emit("changed");
  } catch (cause) {
    actionError.value =
      cause instanceof Error ? cause.message : "That change did not work.";
  } finally {
    busy.value = null;
  }
}
async function makeMain(v: VersionOverview) {
  const wasMainPage = onMainPage.value;
  await act(v, () => makeMainCopy(v.game_id));
  // stay on the game's main page, which is now the chosen copy
  if (wasMainPage && !actionError.value)
    await router.push(`/games/${v.game_id}`);
}
function listSeparately(v: VersionOverview, separately: boolean) {
  if (!v.relationship_id) return;
  return act(v, () =>
    setRelationshipDisplay(v.relationship_id!, separately ? "link" : "default"),
  );
}
async function remove(v: VersionOverview) {
  const ok = await confirm({
    title: "Remove this copy from the game",
    message: `${copyTitle(v)} becomes its own game again. Its saves, playtime, notes and imports stay with it, and it won't be suggested for ${props.title} again.`,
    confirmLabel: "Remove copy",
  });
  if (ok) await act(v, () => splitCopy(v.game_id));
}
async function changed(): Promise<void> {
  adding.value = false;
  await load();
  emit("changed");
}
defineExpose({ load });
</script>

<template>
  <section class="copies" aria-labelledby="copies-title">
    <header>
      <div>
        <h2 id="copies-title">Copies &amp; versions</h2>
        <p class="lead">
          <template v-if="many">
            {{ versions.length }} copies tracked as one game.
          </template>
          <template v-else>
            Own this on another store or platform? Add it as a copy to see its
            saves and playtime here too.
          </template>
        </p>
      </div>
      <div class="header-actions">
        <button
          type="button"
          class="ui-btn ui-btn-secondary"
          @click="adding = true"
        >
          Add a copy
        </button>
        <button
          type="button"
          class="ui-btn ui-btn-ghost"
          :aria-label="`Advanced identity tools for ${title}`"
          @click="managing = true"
        >
          Advanced
        </button>
      </div>
    </header>

    <p v-if="loading && !overview" class="hint" role="status">Loading…</p>
    <div v-else-if="error" class="error" role="alert">
      <span>{{ error }}</span>
      <button type="button" class="ui-btn ui-btn-secondary" @click="load">
        Retry
      </button>
    </div>
    <template v-else-if="overview">
      <p v-if="many && !onMainPage && main" class="notice">
        This is one copy of
        <RouterLink :to="`/games/${main.game_id}`">{{ main.title }}</RouterLink
        >. Its own saves, playtime and imports are shown here.
      </p>

      <dl v-if="many" class="overall" aria-label="Across all copies">
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
          <dt>Completion</dt>
          <dd data-testid="overall-completion">
            {{ overview.aggregate.completion.summary }}
          </dd>
        </div>
        <div v-if="overview.aggregate.achievements.total">
          <dt>Achievements</dt>
          <dd>
            {{ overview.aggregate.achievements.unlocked }} /
            {{ overview.aggregate.achievements.total }}
          </dd>
        </div>
      </dl>
      <p
        v-if="many && !overview.aggregate.playtime_is_sum_of_versions"
        class="hint"
      >
        Copies that report the same playtime are counted once.
      </p>

      <p v-if="actionError" class="error" role="alert">{{ actionError }}</p>
      <ul class="list">
        <li
          v-for="v in shown"
          :key="v.game_id"
          data-testid="version-row"
          :aria-busy="busy === v.game_id"
        >
          <div class="row-head">
            <div class="name">
              <strong>{{ copyTitle(v) }}</strong>
              <span v-if="v.is_primary && many" class="tag tag-main">Main</span>
              <span
                v-if="v.game_id === gameId && many && !v.is_primary"
                class="tag"
                >This page</span
              >
              <span v-if="v.display === 'link'" class="tag"
                >Listed separately</span
              >
              <span v-if="v.display === 'hidden'" class="tag">Hidden</span>
              <span v-if="!v.owned" class="tag">Not owned</span>
            </div>
            <div v-if="many" class="row-actions">
              <RouterLink
                v-if="v.game_id !== gameId"
                :to="`/games/${v.game_id}`"
                class="ui-btn ui-btn-ghost ui-btn-sm"
                >Open</RouterLink
              >
              <button
                v-if="!v.is_primary"
                type="button"
                class="ui-btn ui-btn-ghost ui-btn-sm"
                :disabled="busy !== null"
                @click="makeMain(v)"
              >
                Make main
              </button>
              <button
                v-if="!v.is_primary && v.relationship_id"
                type="button"
                class="ui-btn ui-btn-ghost ui-btn-sm"
                :disabled="busy !== null"
                @click="listSeparately(v, v.display !== 'link')"
              >
                {{ v.display === "link" ? "Show together" : "List separately" }}
              </button>
              <button
                type="button"
                class="ui-btn ui-btn-ghost ui-btn-sm danger"
                :disabled="busy !== null"
                @click="remove(v)"
              >
                Remove
              </button>
            </div>
          </div>
          <p v-if="v.title !== title" class="sub">{{ v.title }}</p>
          <dl class="own">
            <div>
              <dt>Playtime</dt>
              <dd>{{ hoursLabel(v.playtime_seconds) }}</dd>
            </div>
            <div>
              <dt>Last played</dt>
              <dd>{{ dayLabel(v.last_played_at) }}</dd>
            </div>
            <div>
              <dt>Completed</dt>
              <dd>{{ v.completed ? "Yes" : "No" }}</dd>
            </div>
            <div v-if="v.achievements.total">
              <dt>Achievements</dt>
              <dd>
                {{ v.achievements.unlocked }} / {{ v.achievements.total }}
              </dd>
            </div>
          </dl>
          <p v-if="syncedFrom(v).length" class="sub">
            Synced from {{ syncedFrom(v).join(", ") }},
            {{ agoLabel(v.last_observed_at) }}
          </p>
        </li>
      </ul>
      <button
        v-if="hiddenCount"
        type="button"
        class="ui-btn ui-btn-ghost ui-btn-sm"
        :aria-pressed="showHidden"
        @click="showHidden = !showHidden"
      >
        {{ showHidden ? "Hide" : "Show" }} {{ hiddenCount }} hidden
        {{ hiddenCount === 1 ? "copy" : "copies" }}
      </button>
    </template>

    <GameAddCopyDialog
      v-if="adding"
      :game-id="gameId"
      :title="title"
      :members="memberIds"
      @close="adding = false"
      @added="changed"
    />
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
.copies {
  padding: 20px;
  border: 1px solid var(--ui-border);
  border-radius: 12px;
  margin-bottom: 24px;
}
header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 12px;
}
.header-actions,
.row-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
h2 {
  margin: 0;
  font-size: 1.15rem;
}
.lead,
.hint,
.sub {
  color: var(--ui-dim);
  font-size: 0.85rem;
  margin: 4px 0 0;
}
.notice {
  margin: 14px 0 0;
  padding: 10px 12px;
  border-radius: 10px;
  border: 1px solid var(--ui-accent-line);
  background: var(--ui-accent-soft);
}
.notice a,
.name a {
  color: var(--ui-accent-text);
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
  grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
  gap: 12px 16px;
}
.overall {
  margin-top: 14px;
  padding: 14px;
  border-radius: 10px;
  background: var(--ui-accent-soft);
}
.own {
  margin-top: 8px;
}
.list {
  list-style: none;
  padding: 0;
  margin: 8px 0 0;
}
.list li {
  padding: 14px 0;
  border-top: 1px solid var(--ui-border);
}
.list li:first-child {
  border-top: 0;
}
.row-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 8px;
}
.name {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  overflow-wrap: anywhere;
  min-width: 0;
}
.tag {
  padding: 1px 7px;
  border: 1px solid var(--ui-border);
  border-radius: var(--ui-radius-control);
  color: var(--ui-dim);
  font-size: 0.72rem;
}
.tag-main {
  border-color: var(--ui-accent-line);
  color: var(--ui-text);
  background: var(--ui-accent-soft);
}
.danger {
  color: var(--ui-danger);
}
</style>
