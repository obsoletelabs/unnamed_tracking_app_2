<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from "vue";
import {
  fetchReviewQueue,
  keepReviewEntrySeparate,
  mergeReviewEntry,
  relateReviewEntry,
  runIdentityMatching,
  type ReviewEntry,
} from "../../services/identityReview";
import type { ReviewVerdict } from "../../utils/reviewVerdict";
import ReviewEntryCard from "./ReviewEntryCard.vue";

const emit = defineEmits<{ (event: "ids", ids: string[]): void }>();
const entries = ref<ReviewEntry[]>([]);
const busy = ref<string | null>(null);
const error = ref<string | null>(null);
const loaded = ref(false);
const request = new AbortController();

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
  verdict: ReviewVerdict,
): Promise<void> {
  busy.value = entry.id;
  error.value = null;
  try {
    if (verdict.action === "same")
      await mergeReviewEntry(
        entry.id,
        verdict.mainId,
        verdict.display,
        verdict.sameCopy,
      );
    else if (verdict.action === "related")
      await relateReviewEntry(entry.id, verdict);
    else await keepReviewEntrySeparate(entry.id);
    entries.value = entries.value.filter((item) => item.id !== entry.id);
  } catch (cause) {
    error.value =
      cause instanceof Error ? cause.message : "Could not save your choice.";
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
    <ReviewEntryCard
      v-for="entry in entries"
      :key="entry.id"
      :entry="entry"
      :busy="busy !== null"
      @resolve="resolve(entry, $event)"
    />
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
.queue-error {
  color: var(--ui-danger);
}
</style>
