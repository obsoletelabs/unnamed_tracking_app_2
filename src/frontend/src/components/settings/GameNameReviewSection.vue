<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from "vue";
import {
  fetchNameReviews,
  keepGameName,
  repairGameNames,
  updateGameName,
  type NameReview,
} from "../../services/gameNames";

const reviews = ref<NameReview[]>([]);
const loading = ref(false);
const loaded = ref(false);
const busy = ref<string | null>(null);
const error = ref<string | null>(null);
const notice = ref<string | null>(null);
const request = new AbortController();

async function load(): Promise<void> {
  loading.value = true;
  error.value = null;
  try {
    reviews.value = await fetchNameReviews(request.signal);
    loaded.value = true;
  } catch (cause) {
    if (!request.signal.aborted)
      error.value =
        cause instanceof Error ? cause.message : "Could not load name changes.";
  } finally {
    loading.value = false;
  }
}

async function answer(review: NameReview, update: boolean): Promise<void> {
  busy.value = review.id;
  error.value = null;
  try {
    await (update ? updateGameName(review.id) : keepGameName(review.id));
    reviews.value = reviews.value.filter((item) => item.id !== review.id);
  } catch (cause) {
    error.value =
      cause instanceof Error ? cause.message : "Could not save your choice.";
  } finally {
    busy.value = null;
  }
}

async function repair(): Promise<void> {
  busy.value = "repair";
  error.value = null;
  notice.value = null;
  try {
    const result = await repairGameNames();
    notice.value =
      `Checked ${result.checked} games: ${result.replaced.length} renamed, ` +
      `${result.proposed.length} sent for review, ${result.unresolved.length} ` +
      "still waiting for a better name.";
    await load();
  } catch (cause) {
    error.value =
      cause instanceof Error ? cause.message : "Could not repair names.";
  } finally {
    busy.value = null;
  }
}

onMounted(load);
onBeforeUnmount(() => request.abort());
</script>

<template>
  <section class="name-review" aria-labelledby="name-review-title">
    <div class="review-heading">
      <div>
        <h2 id="name-review-title">Game name changes</h2>
        <p class="intro">
          A source reported a different name for a game whose name you
          protected. Update takes the new name; Keep current remembers your
          choice. Placeholder names such as “Steam App 12345” are replaced
          automatically as soon as a real name is known.
        </p>
      </div>
      <button
        class="ui-btn ui-btn-secondary"
        type="button"
        :disabled="busy !== null || loading"
        @click="repair"
      >
        {{ busy === "repair" ? "Checking…" : "Repair placeholder names" }}
      </button>
    </div>
    <p v-if="error" class="review-error" role="alert">{{ error }}</p>
    <p v-if="notice" role="status">{{ notice }}</p>
    <p v-if="loading && !loaded" role="status">Loading name changes…</p>
    <p v-else-if="loaded && !reviews.length" role="status">
      No name changes to review.
    </p>
    <article v-for="review in reviews" :key="review.id" class="name-change">
      <dl>
        <dt>Current name</dt>
        <dd>
          <RouterLink :to="`/games/${review.game_id}`">{{
            review.current_title
          }}</RouterLink>
        </dd>
        <dt>Reported name</dt>
        <dd>{{ review.proposed_title }}</dd>
      </dl>
      <div class="actions">
        <button
          class="ui-btn ui-btn-primary"
          type="button"
          :disabled="busy !== null"
          @click="answer(review, true)"
        >
          Update
        </button>
        <button
          class="ui-btn ui-btn-secondary"
          type="button"
          :disabled="busy !== null"
          @click="answer(review, false)"
        >
          Keep current
        </button>
      </div>
    </article>
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
.intro {
  margin: 0 0 18px;
  color: var(--ui-dim);
  line-height: 1.5;
}
.review-heading .ui-btn,
.actions .ui-btn {
  flex-shrink: 0;
  white-space: nowrap;
}
.name-change {
  border: 1px solid var(--ui-border);
  border-radius: 12px;
  margin-bottom: 16px;
  padding: 18px;
}
dl {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  gap: 6px 12px;
  margin: 0;
}
dt {
  color: var(--ui-dim);
}
dd {
  margin: 0;
  overflow-wrap: anywhere;
}
a {
  color: var(--ui-accent-text);
  font-weight: 700;
}
.actions {
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
  .name-change {
    padding: 14px;
  }
}
</style>
