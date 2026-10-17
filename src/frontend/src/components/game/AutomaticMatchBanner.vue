<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from "vue";
import {
  fetchAutomaticMerges,
  keepAutomaticMerge,
  undoAutomaticMerge,
  type AutomaticMerge,
} from "../../services/identityReview";

const merges = ref<AutomaticMerge[]>([]);
const busy = ref<string | null>(null);
const error = ref<string | null>(null);
const request = new AbortController();

function label(merge: AutomaticMerge): string {
  const names = [merge.into, merge.absorbed].map((game) =>
    game.source
      ? game.source.charAt(0).toUpperCase() + game.source.slice(1)
      : "Manual entry",
  );
  const [first, second] = names;
  return first === second
    ? `Two “${merge.into.title}” entries appear to be the same tracked copy.`
    : `${first} and ${second} appear to be the same tracked copy of “${merge.into.title}”.`;
}

async function load(): Promise<void> {
  try {
    merges.value = await fetchAutomaticMerges(request.signal);
  } catch {
    /* A missing banner never blocks the page. */
  }
}

async function act(
  merge: AutomaticMerge,
  action: (id: string) => Promise<void>,
  failure: string,
): Promise<void> {
  busy.value = merge.id;
  error.value = null;
  try {
    await action(merge.id);
    merges.value = merges.value.filter((item) => item.id !== merge.id);
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : failure;
  } finally {
    busy.value = null;
  }
}

onMounted(load);
onBeforeUnmount(() => request.abort());
defineExpose({ load });
</script>

<template>
  <section
    v-if="merges.length"
    class="match-banner"
    aria-label="Automatic matches"
  >
    <p v-if="error" class="banner-error" role="alert">{{ error }}</p>
    <div
      v-for="merge in merges"
      :key="merge.id"
      class="banner-row"
      role="status"
    >
      <p>
        {{ label(merge) }}
        <span class="why"
          >Same title, release, edition and platform, with identical
          playtime.</span
        >
      </p>
      <div class="banner-actions">
        <button
          class="ui-btn ui-btn-secondary"
          type="button"
          :disabled="busy !== null"
          @click="act(merge, undoAutomaticMerge, 'Could not undo.')"
        >
          Undo
        </button>
        <button
          class="ui-btn ui-btn-ghost"
          type="button"
          :disabled="busy !== null"
          @click="act(merge, keepAutomaticMerge, 'Could not confirm.')"
        >
          Looks right
        </button>
      </div>
    </div>
  </section>
</template>

<style scoped>
.match-banner {
  background: var(--ui-info-soft);
  border: 1px solid var(--ui-border);
  border-radius: 12px;
  color: var(--ui-text);
  margin-bottom: 16px;
  padding: 10px 14px;
}
.banner-row {
  align-items: center;
  display: flex;
  gap: 12px;
  justify-content: space-between;
}
.banner-row p {
  margin: 6px 0;
}
.why {
  color: var(--ui-dim);
  display: block;
  font-size: 0.8rem;
}
.banner-actions {
  display: flex;
  flex-shrink: 0;
  gap: 8px;
}
.banner-error {
  color: var(--ui-danger);
  margin: 0 0 6px;
}
@media (max-width: 600px) {
  .banner-row {
    align-items: flex-start;
    flex-direction: column;
  }
}
</style>
