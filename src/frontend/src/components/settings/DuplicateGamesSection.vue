<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from "vue";
import GroupGameCopiesDialog from "../game/GroupGameCopiesDialog.vue";
import {
  fetchGameDuplicates,
  keepDuplicateGames,
  type DuplicatePair,
} from "../../services/gameDuplicates";

const pairs = ref<DuplicatePair[]>([]);
const hasMore = ref(false);
const loading = ref(false);
const loaded = ref(false);
const busy = ref<string | null>(null);
const error = ref<string | null>(null);
const grouping = ref<DuplicatePair | null>(null);
const request = new AbortController();

function pairKey(pair: DuplicatePair): string {
  return `${pair.first.id}:${pair.second.id}`;
}

async function load(): Promise<void> {
  loading.value = true;
  error.value = null;
  try {
    const result = await fetchGameDuplicates(request.signal);
    pairs.value = result.pairs;
    hasMore.value = result.has_more;
    loaded.value = true;
  } catch (cause) {
    if (!request.signal.aborted)
      error.value =
        cause instanceof Error ? cause.message : "Could not check duplicates.";
  } finally {
    loading.value = false;
  }
}

async function keepBoth(pair: DuplicatePair): Promise<void> {
  busy.value = pairKey(pair);
  error.value = null;
  try {
    await keepDuplicateGames(pair);
    pairs.value = pairs.value.filter((item) => pairKey(item) !== pairKey(pair));
  } catch (cause) {
    error.value =
      cause instanceof Error ? cause.message : "Could not keep both games.";
  } finally {
    busy.value = null;
  }
}

onMounted(load);
onBeforeUnmount(() => request.abort());
async function grouped(): Promise<void> {
  grouping.value = null;
  await load();
}
</script>

<template>
  <section class="duplicate-review" aria-labelledby="duplicate-review-title">
    <div class="review-heading">
      <div>
        <h2 id="duplicate-review-title">Possible duplicate games</h2>
        <p class="intro">
          Compare entries from your library and imports. Similar titles can be
          different editions or copies. Group owned copies under a main game, or
          keep separate entries to remember your choice during future checks.
        </p>
      </div>
      <button
        class="ui-btn ui-btn-secondary"
        type="button"
        :disabled="loading || busy !== null"
        @click="load"
      >
        {{ loading ? "Checking…" : "Check again" }}
      </button>
    </div>
    <p v-if="error" class="review-error" role="alert">{{ error }}</p>
    <p v-if="loading && !loaded" role="status">Checking your library…</p>
    <p v-else-if="loaded && !pairs.length && !hasMore" role="status">
      No possible duplicates to review.
    </p>
    <article v-for="pair in pairs" :key="pairKey(pair)" class="duplicate-pair">
      <p class="intro">
        {{
          pair.reason === "shared_identity"
            ? "A provider identity matches."
            : "These titles match."
        }}
        Review both entries before deciding.
      </p>
      <div class="comparison">
        <div
          v-for="game in [pair.first, pair.second]"
          :key="game.id"
          class="game-summary"
        >
          <RouterLink :to="`/games/${game.id}`" class="game-title">{{
            game.title
          }}</RouterLink>
          <dl>
            <dt>Source</dt>
            <dd>{{ game.source || "Manual entry" }}</dd>
            <dt>Platform</dt>
            <dd>{{ game.platform || "Not recorded" }}</dd>
            <dt>Release</dt>
            <dd>{{ game.release_date?.slice(0, 4) || "Not recorded" }}</dd>
            <dt>Status</dt>
            <dd>{{ game.status.toLowerCase().replaceAll("_", " ") }}</dd>
            <dt>Playtime</dt>
            <dd>
              {{ Math.round(game.playtime_seconds / 60).toLocaleString() }} min
            </dd>
            <template v-if="game.external_id">
              <dt>Import ID</dt>
              <dd>{{ game.external_id }}</dd>
            </template>
            <template
              v-for="(id, provider) in game.provider_ids"
              :key="provider"
            >
              <dt>{{ provider }}</dt>
              <dd>{{ id }}</dd>
            </template>
          </dl>
          <p v-if="game.locked_fields.length" class="locks">
            {{ game.locked_fields.length }} manually protected field{{
              game.locked_fields.length === 1 ? "" : "s"
            }}
          </p>
        </div>
      </div>
      <div class="pair-actions">
        <button
          class="ui-btn ui-btn-primary"
          type="button"
          :disabled="busy !== null || loading"
          @click="grouping = pair"
        >
          Group owned copies
        </button>
        <button
          class="ui-btn ui-btn-secondary"
          type="button"
          :disabled="busy !== null || loading"
          @click="keepBoth(pair)"
        >
          {{ busy === pairKey(pair) ? "Saving…" : "Keep both" }}
        </button>
      </div>
    </article>
    <p v-if="hasMore" class="intro">
      More pairs are available. Check again after reviewing these entries.
    </p>
    <GroupGameCopiesDialog
      v-if="grouping"
      :games="[grouping.first, grouping.second]"
      @close="grouping = null"
      @grouped="grouped"
    />
  </section>
</template>

<style scoped>
.review-heading {
  display: flex;
  align-items: flex-start;
  gap: 16px;
  justify-content: space-between;
}
h2 {
  margin: 0 0 8px;
  font-size: 1.15rem;
}
.intro,
.locks {
  color: var(--ui-dim);
  line-height: 1.5;
}
.intro {
  margin: 0 0 18px;
}
.review-heading .ui-btn,
.pair-actions .ui-btn {
  flex-shrink: 0;
  white-space: nowrap;
}
.duplicate-pair {
  border: 1px solid var(--ui-border);
  border-radius: 12px;
  margin-bottom: 16px;
  padding: 18px;
}
.comparison {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 24px;
}
.game-summary {
  min-width: 0;
}
.game-title {
  display: block;
  font-weight: 700;
  color: var(--ui-accent-text);
  overflow-wrap: anywhere;
}
dl {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  gap: 6px 12px;
  font-size: 0.85rem;
}
dt {
  color: var(--ui-dim);
  overflow-wrap: anywhere;
}
dd {
  margin: 0;
  overflow-wrap: anywhere;
}
.locks {
  margin: 0;
  font-size: 0.8rem;
}
.pair-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  justify-content: flex-end;
  margin-top: 16px;
}
.review-error {
  color: var(--ui-danger);
}
@media (max-width: 600px) {
  .review-heading {
    flex-direction: column;
    gap: 0;
    margin-bottom: 18px;
  }
  .comparison {
    grid-template-columns: minmax(0, 1fr);
    gap: 18px;
  }
  .game-summary + .game-summary {
    border-top: 1px solid var(--ui-border);
    padding-top: 18px;
  }
  .duplicate-pair {
    padding: 14px;
  }
}
</style>
