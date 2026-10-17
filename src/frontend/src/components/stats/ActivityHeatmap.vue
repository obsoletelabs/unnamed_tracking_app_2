<script setup lang="ts">
// The last 52 weeks as a grid, one square per day, shaded by how many
// episodes were checked off or achievements unlocked that day. Weeks are
// columns (Sunday at the top) and the days come from the server's own window,
// so the grid, its counts and the day dialog share one calendar. Levels are
// quartiles of the busiest day. Every square is a button: click, Enter or
// Space opens that day, and the arrow keys move between days.
import { computed, nextTick, onMounted, ref } from "vue";
import ActivityDayDialog from "./ActivityDayDialog.vue";
import type { ActivityDayCounts } from "../../services/mediaStats";
import {
  addDays,
  describeCounts,
  longDay,
  monthMarks,
} from "../../utils/activityDays";

const props = defineProps<{
  days: ActivityDayCounts[];
  start: string;
  end: string;
  timezone: string;
}>();

interface Cell {
  key: string;
  count: number;
  label: string;
  level: number;
}

const weeks = computed<Cell[][]>(() => {
  const byDay = new Map(props.days.map((d) => [d.date, d]));
  const max = Math.max(1, ...props.days.map((d) => d.count));
  const out: Cell[][] = [];
  for (let start = props.start; start <= props.end; start = addDays(start, 7)) {
    const week: Cell[] = [];
    for (let i = 0; i < 7; i++) {
      const key = addDays(start, i);
      if (key > props.end) break;
      const day = byDay.get(key);
      const count = day?.count ?? 0;
      week.push({
        key,
        count,
        label: `${longDay(key)}: ${describeCounts(day ?? {})}`,
        level: !count ? 0 : Math.min(4, Math.ceil((count / max) * 4)),
      });
    }
    out.push(week);
  }
  return out;
});
const marks = computed(
  () =>
    new Map(
      monthMarks(weeks.value.map((week) => week[0]!.key)).map((m) => [
        m.week,
        m.label,
      ]),
    ),
);

const opened = ref<string | null>(null);
// roving tabindex: one square is in the tab order, the arrows move it
const focusable = ref(props.end);
const scroller = ref<HTMLElement | null>(null);

function move(key: string, delta: number) {
  const target = addDays(key, delta);
  if (target < props.start || target > props.end) return;
  focusable.value = target;
  void nextTick(() =>
    scroller.value
      ?.querySelector<HTMLElement>(`[data-day="${target}"]`)
      ?.focus(),
  );
}
function onKey(event: KeyboardEvent, key: string) {
  const step: Record<string, number> = {
    ArrowUp: -1,
    ArrowDown: 1,
    ArrowLeft: -7,
    ArrowRight: 7,
  };
  const delta = step[event.key];
  if (delta !== undefined) {
    event.preventDefault();
    move(key, delta);
  } else if (event.key === "Home" || event.key === "End") {
    event.preventDefault();
    const edge = event.key === "Home" ? props.start : props.end;
    move(edge, 0);
  }
}
// start on the most recent weeks, where the activity usually is
onMounted(() => {
  if (scroller.value) scroller.value.scrollLeft = scroller.value.scrollWidth;
});
</script>

<template>
  <div ref="scroller" class="heat-scroll">
    <div class="heat" role="group" aria-label="Activity by day">
      <div v-for="(week, wi) in weeks" :key="wi" class="heat-week">
        <span class="heat-month" aria-hidden="true">{{ marks.get(wi) }}</span>
        <button
          v-for="cell in week"
          :key="cell.key"
          type="button"
          class="heat-cell"
          :class="`l${cell.level}`"
          :data-day="cell.key"
          :title="cell.label"
          :aria-label="cell.label"
          :tabindex="cell.key === focusable ? 0 : -1"
          @click="opened = cell.key"
          @focus="focusable = cell.key"
          @keydown="onKey($event, cell.key)"
        ></button>
      </div>
    </div>
  </div>
  <ActivityDayDialog
    v-if="opened"
    :day="opened"
    :timezone="timezone"
    @close="opened = null"
  />
</template>

<style scoped>
.heat-scroll {
  overflow-x: auto;
  max-width: 100%;
  padding-bottom: 4px;
}
.heat {
  display: flex;
  gap: 3px;
  width: max-content;
}
.heat-week {
  display: flex;
  flex-direction: column;
  gap: 3px;
}
.heat-month {
  height: 16px;
  /* the label spills over the empty columns to its right */
  width: 12px;
  overflow: visible;
  white-space: nowrap;
  color: var(--ui-dim);
  font-size: var(--ui-font-small);
  line-height: 16px;
}
.heat-cell {
  width: 12px;
  height: 12px;
  padding: 0;
  border: 0;
  border-radius: 3px;
  background: var(--ui-surface-2);
  cursor: pointer;
}
.heat-cell:hover {
  outline: 1px solid var(--ui-dim);
}
.heat-cell:focus-visible {
  outline: 2px solid var(--ui-accent-text, var(--ui-accent));
  outline-offset: 2px;
}
.heat-cell.l1 {
  background: color-mix(in srgb, var(--ui-accent) 28%, transparent);
}
.heat-cell.l2 {
  background: color-mix(in srgb, var(--ui-accent) 50%, transparent);
}
.heat-cell.l3 {
  background: color-mix(in srgb, var(--ui-accent) 75%, transparent);
}
.heat-cell.l4 {
  background: var(--ui-accent);
}
@media (max-width: 760px), (pointer: coarse) {
  /* larger touch targets; the scroller keeps the whole year reachable */
  .heat-cell {
    width: 16px;
    height: 16px;
  }
  .heat-month {
    width: 16px;
  }
}
</style>
