<script setup lang="ts">
import { computed, ref } from "vue";
import {
  adjustPlaytime,
  clearAdjustment,
  groupCounters,
  setCounterPriority,
  ungroupObservation,
  type CounterView,
} from "../../../services/gameIdentity";
import {
  agoLabel,
  hoursLabel,
  sourceName,
} from "../../../utils/identityDisplay";
import { titleOf, type IdentityTabProps } from "../../../utils/identityTabs";

const props = defineProps<IdentityTabProps>();

const METRICS: [string, string][] = [
  ["playtime", "Playtime"],
  ["last_played", "Last played"],
  ["achievements", "Achievements"],
  ["completion", "Completion"],
  ["ownership", "Ownership"],
  ["installed", "Installed"],
];
const sections = computed(() =>
  METRICS.map(([key, label]) => ({
    key,
    label,
    counters: props.data.counters[key] ?? [],
  })).filter((s) => s.counters.length),
);

const hours = ref<Record<string, string>>({});
const priority = ref<Record<string, string>>({});
const groupWith = ref<Record<string, string>>({});

function show(metric: string, value: unknown): string {
  if (value === null || value === undefined) return "Unknown";
  if (metric === "playtime" && typeof value === "number")
    return hoursLabel(value);
  if (metric === "last_played" && typeof value === "number")
    return new Date(value * 1000).toLocaleDateString();
  if (typeof value === "boolean") return value ? "Yes" : "No";
  return String(value);
}
async function setHours(counter: CounterView): Promise<void> {
  const value = Number(hours.value[counter.counter_id]);
  if (!Number.isFinite(value) || value < 0) return;
  const done = await props.act(() =>
    adjustPlaytime(
      counter.game_id,
      Math.round(value * 3600),
      counter.counter_id,
    ),
  );
  if (done) delete hours.value[counter.counter_id];
}
function setPriority(counter: CounterView, observationId: string) {
  const value = Number(priority.value[observationId]);
  if (!Number.isInteger(value) || value < 0) return Promise.resolve(false);
  return props.act(() =>
    setCounterPriority(counter.counter_id, observationId, value),
  );
}
function group(counter: CounterView) {
  const other = groupWith.value[counter.counter_id];
  if (!other) return Promise.resolve(false);
  return props.act(() => groupCounters(counter.counter_id, other));
}
</script>

<template>
  <div class="tab">
    <p class="sub">
      A counter is one thing that sources report, such as playtime. Sources in
      the same counter are never added together: the lowest priority number
      wins, then the freshest report. Independent counters add up.
    </p>
    <p v-if="!sections.length" class="sub">
      No source reports any values for this game yet, so its saved values are
      shown everywhere.
    </p>
    <section
      v-for="s in sections"
      :key="s.key"
      :aria-label="`${s.label} counters`"
    >
      <h3>{{ s.label }}</h3>
      <ul class="rows">
        <li v-for="c in s.counters" :key="c.counter_id" class="metric">
          <div class="grow">
            <strong>{{ show(s.key, c.effective) }}</strong>
            <span v-if="c.adjustment" class="tag">Adjusted by you</span>
            <p class="sub">
              From {{ sourceName(c.selected_source) }} on
              {{ titleOf(data, c.game_id) }}
              <template v-if="c.adjustment && c.raw !== c.effective">
                · source says {{ show(s.key, c.raw) }}
              </template>
            </p>
            <ul class="members">
              <li v-for="m in c.members" :key="m.observation_id">
                <span>
                  {{ sourceName(m.source) }}:
                  {{ show(s.key, m.value) }}
                  <span v-if="m.selected" class="tag">Used</span>
                  <span v-if="!m.available" class="tag">Unavailable</span>
                  <span class="sub">, {{ agoLabel(m.observed_at) }}</span>
                </span>
                <label class="inline">
                  <span>Priority</span>
                  <input
                    v-model="priority[m.observation_id]"
                    class="ui-field num"
                    type="number"
                    min="0"
                    :placeholder="String(m.priority)"
                    :aria-label="`Priority for ${sourceName(m.source)}`"
                    :disabled="busy"
                    @keydown.enter.prevent="setPriority(c, m.observation_id)"
                    @change="setPriority(c, m.observation_id)"
                  />
                </label>
                <button
                  v-if="c.members.length > 1"
                  type="button"
                  class="ui-btn ui-btn-ghost"
                  :disabled="busy"
                  :aria-label="`Ungroup ${sourceName(m.source)} from this counter`"
                  @click="
                    act(() =>
                      ungroupObservation(c.counter_id, m.observation_id),
                    )
                  "
                >
                  Ungroup
                </button>
              </li>
            </ul>
            <p v-for="x in c.conflicts" :key="x.source + x.reason" class="sub">
              {{ sourceName(x.source) }} reported {{ show(s.key, x.value) }} but
              was not used: {{ x.reason }}
            </p>
          </div>
          <form
            v-if="s.key === 'playtime'"
            class="form-inline"
            @submit.prevent="setHours(c)"
          >
            <label class="inline">
              <span>Correct to (hours)</span>
              <input
                v-model="hours[c.counter_id]"
                class="ui-field num"
                type="number"
                min="0"
                step="0.1"
                :disabled="busy"
              />
            </label>
            <button
              class="ui-btn ui-btn-secondary"
              type="submit"
              :disabled="busy || !hours[c.counter_id]"
            >
              Save
            </button>
            <button
              v-if="c.adjustment"
              type="button"
              class="ui-btn ui-btn-ghost"
              :disabled="busy"
              @click="act(() => clearAdjustment(c.counter_id))"
            >
              Remove my correction
            </button>
          </form>
          <label v-if="s.counters.length > 1" class="inline">
            <span>Same counter as</span>
            <select
              v-model="groupWith[c.counter_id]"
              class="ui-field"
              @change="group(c)"
            >
              <option value="">Keep independent</option>
              <option
                v-for="other in s.counters.filter(
                  (x) => x.counter_id !== c.counter_id,
                )"
                :key="other.counter_id"
                :value="other.counter_id"
              >
                {{ sourceName(other.selected_source) }} on
                {{ titleOf(data, other.game_id) }}
              </option>
            </select>
          </label>
        </li>
      </ul>
    </section>
  </div>
</template>

<style scoped src="./identityTab.css"></style>
<style scoped>
.members {
  list-style: none;
  padding: 0;
  margin: 8px 0 0;
}
.members li {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px 12px;
  padding: 4px 0;
}
.members .num {
  width: 5.5rem;
}
.form-inline {
  display: flex;
  align-items: flex-end;
  flex-wrap: wrap;
  gap: 8px;
}
</style>
