<script setup lang="ts">
import { ref, onMounted, onBeforeUnmount } from "vue";
import {
  fetchJobs,
  updateJob,
  runJobNow,
  controlJob,
} from "../../services/settings";
import type { CleanupJob, JobControl } from "../../services/settings";

const props = defineProps<{ personal?: boolean }>();
type Unit = "minutes" | "hours" | "days" | "weeks";
const UNIT_MINUTES: Record<Unit, number> = {
  minutes: 1,
  hours: 60,
  days: 24 * 60,
  weeks: 7 * 24 * 60,
};

const jobs = ref<CleanupJob[]>([]);
const jobError = ref<string | null>(null);
const intervalErrors = ref<Record<string, string>>({});
const actionErrors = ref<Record<string, string>>({});
const saving = ref<Record<string, boolean>>({});
const starting = ref<Record<string, boolean>>({});
let refreshTimer: ReturnType<typeof setInterval> | null = null;
let loading = false;
// what is typed in each job's "every" fields, kept apart from what is saved
const drafts = ref<Record<string, { amount: number; unit: Unit }>>({});

// shows a saved number of minutes in the largest unit that divides it evenly
function split(minutes: number): { amount: number; unit: Unit } {
  if (minutes % UNIT_MINUTES.weeks === 0)
    return { amount: minutes / UNIT_MINUTES.weeks, unit: "weeks" };
  if (minutes % UNIT_MINUTES.days === 0)
    return { amount: minutes / UNIT_MINUTES.days, unit: "days" };
  if (minutes % UNIT_MINUTES.hours === 0)
    return { amount: minutes / UNIT_MINUTES.hours, unit: "hours" };
  return { amount: minutes, unit: "minutes" };
}
function resetDrafts() {
  drafts.value = Object.fromEntries(
    jobs.value.map((j) => [j.id, split(j.intervalMinutes)]),
  );
}
async function loadJobs(retainDrafts = false) {
  if (loading) return;
  loading = true;
  try {
    jobError.value = null;
    jobs.value = await fetchJobs(props.personal);
    if (!retainDrafts) resetDrafts();
    else
      jobs.value.forEach((job) => {
        drafts.value[job.id] ??= split(job.intervalMinutes);
      });
  } catch (err) {
    jobError.value = err instanceof Error ? err.message : "Failed to load jobs";
  } finally {
    loading = false;
  }
}
async function changeJob(
  job: CleanupJob,
  changes: { enabled?: boolean; intervalMinutes?: number },
) {
  if (saving.value[job.id]) return;
  const isInterval = changes.intervalMinutes !== undefined;
  const errors = isInterval ? intervalErrors : actionErrors;
  delete errors.value[job.id];
  saving.value[job.id] = true;
  try {
    const saved = await updateJob(job.id, changes, props.personal);
    jobs.value = jobs.value.map((j) => (j.id === saved.id ? saved : j));
    if (isInterval) drafts.value[job.id] = split(saved.intervalMinutes);
  } catch (err) {
    errors.value[job.id] =
      err instanceof Error ? err.message : "Failed to save";
  } finally {
    saving.value[job.id] = false;
  }
}
function saveInterval(job: CleanupJob) {
  const draft = drafts.value[job.id];
  const minutes = Math.round(draft.amount * UNIT_MINUTES[draft.unit]);
  if (
    !Number.isFinite(minutes) ||
    minutes < job.minIntervalMinutes ||
    minutes > job.maxIntervalMinutes
  ) {
    intervalErrors.value[job.id] =
      `Choose between ${describe(job.minIntervalMinutes)} and ${describe(job.maxIntervalMinutes)}.`;
    return;
  }
  delete intervalErrors.value[job.id];
  if (minutes !== job.intervalMinutes)
    void changeJob(job, { intervalMinutes: minutes });
}
function describe(minutes: number): string {
  const { amount, unit } = split(minutes);
  return `${amount} ${amount === 1 ? unit.slice(0, -1) : unit}`;
}
async function runNow(job: CleanupJob) {
  delete actionErrors.value[job.id];
  starting.value[job.id] = true;
  try {
    await runJobNow(job.id, props.personal);
    await loadJobs(true);
  } catch (err) {
    actionErrors.value[job.id] =
      err instanceof Error ? err.message : "Failed to start";
  } finally {
    starting.value[job.id] = false;
  }
}
async function control(job: CleanupJob, action: JobControl) {
  delete actionErrors.value[job.id];
  starting.value[job.id] = true;
  try {
    const saved = await controlJob(job.id, action, props.personal);
    jobs.value = jobs.value.map((j) => (j.id === saved.id ? saved : j));
  } catch (err) {
    actionErrors.value[job.id] =
      err instanceof Error ? err.message : "Failed to update the task";
  } finally {
    starting.value[job.id] = false;
  }
}
const STATUS_TEXT: Record<string, string> = {
  partial: "Partly done. Continues from the saved step.",
  failed: "Last attempt failed. It will retry automatically.",
  cancelled: "Cancelled. Saved progress was kept.",
  disabled: "Turned off because access was removed.",
  paused: "Paused. Resume to continue from the saved step.",
};
function statusNote(job: CleanupJob): string {
  if (job.status === "failed" && !job.nextRunAt)
    return "Last attempt failed. Run now to try again.";
  return STATUS_TEXT[job.status] ?? "";
}
onMounted(() => {
  void loadJobs();
  refreshTimer = setInterval(() => void loadJobs(true), 5000);
});
onBeforeUnmount(() => {
  if (refreshTimer !== null) clearInterval(refreshTimer);
});

function formatTime(epochSeconds: number | null): string {
  if (!epochSeconds) return "Never";
  return new Date(epochSeconds * 1000).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}
</script>

<template>
  <section class="settings-section">
    <h2>{{ personal ? "Background tasks" : "Tasks" }}</h2>
    <p v-if="personal" class="section-hint">
      Choose what runs automatically for your account. Autosync starts off and
      uses your connected accounts and approved plugin permissions. Saved
      imports run even when this page is closed. Each configured Steam,
      PlayStation and RetroAchievements connection gets its own task, as does
      your AniList import, alongside approved plugin tasks. Disconnecting turns
      a task off and keeps its interval and history.
    </p>
    <p v-else class="section-hint">
      Built-in jobs and approved plugin tasks use the app's scheduler. Each one
      can be switched off and given its own schedule. Plugin schedules start
      off.
    </p>

    <div v-if="jobError" class="form-error">{{ jobError }}</div>
    <p v-else-if="personal && !jobs.length" class="section-hint">
      No personal tasks are available yet. Configure a sync-capable service in
      Connections to choose its schedule here.
    </p>
    <div v-for="job in jobs" :key="job.id" class="tile">
      <div class="tile-head">
        <h3>{{ job.name }}</h3>
        <span
          class="status-badge"
          :class="{ on: job.enabled && job.available }"
        >
          {{
            job.running
              ? "Running now"
              : job.paused
                ? "Paused"
                : job.enabled && !job.available
                  ? "Paused"
                  : job.enabled
                    ? "On"
                    : "Off"
          }}
        </span>
      </div>
      <p class="tile-desc">{{ job.description }}</p>
      <p v-if="job.pluginId" class="plugin-source">
        From {{ job.pluginName }} ·
        {{
          personal
            ? "Uses your account's approved background access."
            : "Runs with this plugin's approved background access."
        }}
      </p>
      <p v-if="!job.available" class="unavailable-note" role="status">
        {{ job.unavailableReason }}
      </p>
      <div class="job-controls">
        <label class="job-toggle">
          <input
            type="checkbox"
            :checked="job.enabled"
            :disabled="saving[job.id] || (!job.available && !job.enabled)"
            @change="
              changeJob(job, {
                enabled: ($event.target as HTMLInputElement).checked,
              })
            "
          />
          {{ personal ? "Autosync" : "Run by itself" }}
        </label>
        <div v-if="drafts[job.id]" class="interval-field">
          <label class="job-every">
            Every
            <input
              v-model.number="drafts[job.id].amount"
              type="number"
              min="1"
              class="job-amount"
              :disabled="saving[job.id] || !job.available"
              :aria-invalid="Boolean(intervalErrors[job.id])"
              :aria-describedby="`interval-help-${job.id}`"
              :aria-label="`How often ${job.name} runs`"
              @change="saveInterval(job)"
            />
            <select
              v-model="drafts[job.id].unit"
              class="job-interval"
              :disabled="saving[job.id] || !job.available"
              :aria-invalid="Boolean(intervalErrors[job.id])"
              :aria-describedby="`interval-help-${job.id}`"
              aria-label="Unit of time"
              @change="saveInterval(job)"
            >
              <option value="minutes">minutes</option>
              <option value="hours">hours</option>
              <option value="days">days</option>
              <option value="weeks">weeks</option>
            </select>
          </label>
          <p
            :id="`interval-help-${job.id}`"
            class="interval-help"
            :class="{ invalid: intervalErrors[job.id] }"
            :role="intervalErrors[job.id] ? 'alert' : undefined"
          >
            {{
              intervalErrors[job.id] ||
              `Allowed: ${describe(job.minIntervalMinutes)} to ${describe(job.maxIntervalMinutes)}.`
            }}
          </p>
        </div>
        <button
          type="button"
          class="secondary-button"
          :disabled="job.running || starting[job.id] || !job.available"
          @click="runNow(job)"
        >
          {{ job.running || starting[job.id] ? "Running…" : "Run now" }}
        </button>
        <button
          v-if="job.paused"
          type="button"
          class="secondary-button"
          :disabled="starting[job.id]"
          @click="control(job, 'resume')"
        >
          Resume
        </button>
        <button
          v-else-if="job.enabled || job.running"
          type="button"
          class="secondary-button"
          :disabled="starting[job.id]"
          @click="control(job, 'pause')"
        >
          Pause
        </button>
        <button
          v-if="
            job.running || job.status === 'partial' || job.status === 'failed'
          "
          type="button"
          class="secondary-button"
          :disabled="starting[job.id]"
          @click="control(job, 'cancel')"
        >
          Cancel
        </button>
      </div>
      <p v-if="actionErrors[job.id]" class="form-error" role="alert">
        {{ actionErrors[job.id] }}
      </p>
      <p class="last-run">
        Last run: {{ formatTime(job.lastRunAt) }}
        <template v-if="job.lastSummary"> · {{ job.lastSummary }}</template>
      </p>
      <p v-if="statusNote(job)" class="last-run" role="status">
        {{ statusNote(job) }}
      </p>
      <p class="last-run">
        Last successful run: {{ formatTime(job.lastSuccessAt) }} · Next run:
        {{ job.nextRunAt ? formatTime(job.nextRunAt) : "Not scheduled" }}
      </p>
      <p class="tile-desc job-note">
        "Run now" works whether or not it runs by itself.
      </p>
    </div>
  </section>
</template>

<style scoped>
.settings-section h2 {
  margin: 0 0 12px;
  font: var(--ui-weight-heading) var(--ui-font-heading)/1.4
    var(--ui-font-family);
  color: var(--ui-text);
}
.section-hint {
  color: var(--ui-dim);
  font-size: 0.82rem;
  line-height: 1.6;
  margin: 0 0 20px;
}
.tile {
  background: var(--ui-surface);
  border: 1px solid var(--ui-border-strong);
  border-radius: var(--ui-radius-control);
  padding: 18px 20px;
  margin-bottom: 16px;
}
.tile-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 6px;
}
.tile-head h3 {
  margin: 0;
  font-size: 0.9rem;
  color: var(--ui-text);
}
.status-badge {
  font-size: 0.7rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: var(--ui-dim);
  background: color-mix(in srgb, var(--ui-text) 6%, transparent);
  padding: 3px 10px;
  border-radius: 999px;
  white-space: nowrap;
}
.status-badge.on {
  color: var(--ui-good);
  background: var(--ui-good-soft);
}
.tile-desc {
  color: var(--ui-dim);
  font-size: 0.8rem;
  line-height: 1.5;
  margin: 0 0 12px;
}
.last-run {
  color: var(--ui-dim);
  font-size: 0.76rem;
  margin: 0 0 14px;
}
.form-error {
  color: var(--ui-error);
  font-size: 0.8rem;
  margin: 0 0 12px;
}
.secondary-button {
  background: var(--ui-surface);
  border: 1px solid var(--ui-border);
  color: var(--ui-text);
  border-radius: var(--ui-radius-control);
  padding: 9px 16px;
  font-weight: 600;
  font-size: 0.82rem;
  cursor: pointer;
}
.secondary-button:disabled {
  opacity: 0.6;
  cursor: default;
}
.job-controls {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;
  margin: 4px 0 10px;
}
.job-toggle {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 0.85rem;
  color: var(--ui-text);
}
.job-toggle input {
  accent-color: var(--ui-accent);
}
.job-every {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  font-size: 0.85rem;
  color: var(--ui-text);
}
.job-amount {
  width: 72px;
  background: var(--ui-surface);
  color: var(--ui-text);
  border: 1px solid var(--ui-border);
  border-radius: var(--ui-radius-control);
  padding: 7px 10px;
  font-size: 0.82rem;
}
.job-amount:disabled {
  opacity: 0.5;
}
.job-interval {
  background: var(--ui-surface);
  color: var(--ui-text);
  border: 1px solid var(--ui-border);
  border-radius: var(--ui-radius-control);
  padding: 7px 10px;
  font-size: 0.82rem;
}
.job-interval:disabled {
  opacity: 0.5;
}
.job-note {
  margin: 8px 0 0;
}
.interval-field {
  max-width: 100%;
}
.interval-help {
  color: var(--ui-dim);
  font-size: 0.76rem;
  margin: 8px 0 0;
}
.interval-help.invalid {
  color: var(--ui-error);
}
.job-amount[aria-invalid="true"] {
  border-color: var(--ui-error);
}
.plugin-source {
  color: var(--ui-dim);
  font-size: 0.78rem;
  margin: 0 0 12px;
}
.unavailable-note {
  color: var(--ui-warning);
  background: var(--ui-warning-soft);
  padding: 12px;
  border-radius: var(--ui-radius-control);
}
@media (max-width: 760px) {
  .job-amount,
  .job-interval {
    box-sizing: border-box;
    min-height: 44px;
    font-size: 1rem;
  }
  .job-amount {
    width: 64px;
  }
  .job-controls .secondary-button {
    min-height: 44px;
  }
}
</style>
