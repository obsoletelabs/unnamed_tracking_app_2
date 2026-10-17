<script setup lang="ts">
// What one heatmap square counts: the episodes and achievements of a day plus
// other recorded history, loaded from the same endpoint rule the grid uses.
// Entries link to their own detail pages; nothing is guessed, so a title shows
// how many episodes were recorded, not which ones.
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { RouterLink } from "vue-router";
import UiModal from "../UiModal.vue";
import { fetchActivityDay } from "../../services/mediaStats";
import type { ActivityDay, ActivityDayItem } from "../../services/mediaStats";
import { describeCounts, itemPath, longDay } from "../../utils/activityDays";

const props = defineProps<{ day: string; timezone: string }>();
const emit = defineEmits<{ close: [] }>();

const detail = ref<ActivityDay | null>(null);
const items = ref<ActivityDayItem[]>([]);
const loading = ref(true);
const error = ref<string | null>(null);
let request: AbortController | null = null;

async function load(offset = 0) {
  request?.abort();
  const controller = new AbortController();
  request = controller;
  loading.value = true;
  error.value = null;
  try {
    const page = await fetchActivityDay(
      props.day,
      props.timezone,
      controller.signal,
      offset,
    );
    if (controller.signal.aborted) return;
    detail.value = page;
    items.value = offset ? [...items.value, ...page.items] : page.items;
  } catch (cause) {
    if (controller.signal.aborted) return;
    error.value =
      cause instanceof Error ? cause.message : "Failed to load this day.";
  } finally {
    if (request === controller) loading.value = false;
  }
}
onMounted(() => void load());
onBeforeUnmount(() => request?.abort());

const MEDIA_LABEL = { movie: "Movie", tv: "TV show", anime: "Anime" } as const;
const EVENT_LABEL: Record<string, string> = {
  status_changed: "Status changed",
  rewatched: "Rewatched",
  rated: "Rated",
};
const summary = computed(() =>
  detail.value ? describeCounts(detail.value) : "",
);
const empty = computed(
  () => !loading.value && !error.value && detail.value?.total === 0,
);
const episodes = computed(() =>
  items.value.filter((i) => i.kind === "episodes"),
);
const achievements = computed(() =>
  items.value.filter((i) => i.kind === "achievement"),
);
const history = computed(() => items.value.filter((i) => i.kind === "history"));
function time(unix: number): string {
  return new Date(unix * 1000).toLocaleTimeString(undefined, {
    hour: "numeric",
    minute: "2-digit",
    timeZone: props.timezone,
  });
}
</script>

<template>
  <UiModal
    :title="longDay(day)"
    :description="summary ? `${summary} (${timezone})` : timezone"
    class="activity-day"
    @close="emit('close')"
  >
    <p v-if="loading && !items.length" class="ui-state" role="status">
      Loading…
    </p>
    <p v-if="error" class="ui-state error" role="alert">
      {{ error }}
      <button type="button" class="ui-btn ui-btn-secondary" @click="load(0)">
        Try again
      </button>
    </p>
    <p v-if="empty" class="ui-state">Nothing was recorded on this day.</p>

    <section v-if="episodes.length" aria-labelledby="day-episodes">
      <h3 id="day-episodes">Episodes watched</h3>
      <ul class="day-list">
        <li v-for="item in episodes" :key="`e-${item.media_type}-${item.id}`">
          <template v-if="item.kind === 'episodes'">
            <RouterLink :to="itemPath(item)" @click="emit('close')">
              {{ item.title }}
            </RouterLink>
            <span class="dim"
              >{{ MEDIA_LABEL[item.media_type] }}, {{ item.count }} episode{{
                item.count === 1 ? "" : "s"
              }}</span
            >
          </template>
        </li>
      </ul>
    </section>
    <section v-if="achievements.length" aria-labelledby="day-achievements">
      <h3 id="day-achievements">Achievements unlocked</h3>
      <ul class="day-list">
        <li v-for="item in achievements" :key="`a-${item.id}`">
          <template v-if="item.kind === 'achievement'">
            <RouterLink :to="itemPath(item)" @click="emit('close')">
              {{ item.name }}
            </RouterLink>
            <span class="dim"
              >{{ item.game_title }}, {{ time(item.unlocked_at) }}</span
            >
          </template>
        </li>
      </ul>
    </section>
    <section v-if="history.length" aria-labelledby="day-history">
      <h3 id="day-history">Other history</h3>
      <p class="note">Recorded on this day; not part of the square's count.</p>
      <ul class="day-list">
        <li
          v-for="item in history"
          :key="`h-${item.media_type}-${item.id}-${item.event_type}`"
        >
          <template v-if="item.kind === 'history'">
            <RouterLink :to="itemPath(item)" @click="emit('close')">
              {{ item.title }}
            </RouterLink>
            <span class="dim"
              >{{ EVENT_LABEL[item.event_type] ?? item.event_type
              }}{{ item.detail ? `: ${item.detail}` : ""
              }}{{
                item.event_type === "status_changed" ? "" : ` (${item.count})`
              }}</span
            >
          </template>
        </li>
      </ul>
    </section>

    <p v-if="detail && items.length < detail.total" class="more">
      Showing {{ items.length }} of {{ detail.total }} entries.
      <button
        type="button"
        class="ui-btn ui-btn-secondary"
        :disabled="loading"
        @click="load(detail.next_offset)"
      >
        {{ loading ? "Loading…" : "Show more" }}
      </button>
    </p>
    <template #footer>
      <button
        type="button"
        class="ui-btn ui-btn-primary"
        @click="emit('close')"
      >
        Close
      </button>
    </template>
  </UiModal>
</template>

<style scoped>
h3 {
  margin: 16px 0 8px;
  font-size: var(--ui-font-body);
}
.day-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: 4px;
}
.day-list li {
  display: flex;
  flex-wrap: wrap;
  justify-content: space-between;
  gap: 4px 12px;
  padding: var(--ui-row-padding, 8px) 12px;
  border-radius: var(--ui-radius-row);
  background: var(--ui-surface-2);
  overflow-wrap: anywhere;
}
.day-list a {
  color: var(--ui-accent-text, var(--ui-accent));
  font-weight: 600;
}
.dim,
.note {
  color: var(--ui-dim);
  font-size: var(--ui-font-small);
}
.note {
  margin: 0 0 8px;
}
.more {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;
  margin-top: 16px;
  color: var(--ui-dim);
}
</style>
