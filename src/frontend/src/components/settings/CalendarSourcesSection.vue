<script setup lang="ts">
import { onMounted, onBeforeUnmount, ref, watch } from "vue";
import { currentUser } from "../../state/auth";
import { useConfirm } from "../../state/dialog";
import {
  createCalendarSource,
  deleteCalendarSource,
  fetchCalendarSources,
  refreshCalendarSource,
  sourceStatus,
  updateCalendarSource,
  type CalendarSource,
} from "../../services/calendarSources";

const sources = ref<CalendarSource[]>([]);
const loaded = ref(false);
const remoteEnabled = ref(true);
const error = ref("");
const notice = ref("");
const busy = ref(false);
const editing = ref<string | null>(null);
const name = ref("");
const url = ref("");
const color = ref("#6b9bd2");
const interval = ref(360);
const confirm = useConfirm();
let generation = 0;
let accountEpoch = 0;
async function load() {
  const ticket = ++generation;
  const epoch = accountEpoch;
  error.value = "";
  try {
    const result = await fetchCalendarSources();
    if (ticket !== generation || epoch !== accountEpoch) return;
    sources.value = result.sources;
    remoteEnabled.value = result.remote_enabled;
    loaded.value = true;
  } catch (e) {
    if (ticket !== generation || epoch !== accountEpoch) return;
    error.value =
      e instanceof Error ? e.message : "Could not load subscriptions.";
  }
}
onMounted(load);
onBeforeUnmount(() => {
  ++generation;
  ++accountEpoch;
});
watch(
  () => currentUser.value?.id,
  (id) => {
    ++generation;
    ++accountEpoch;
    sources.value = [];
    loaded.value = false;
    busy.value = false;
    error.value = notice.value = "";
    reset();
    if (id) void load();
  },
);
function edit(source: CalendarSource) {
  editing.value = source.id;
  name.value = source.name;
  url.value = "";
  color.value = source.color;
  interval.value = source.refresh_minutes;
  notice.value = "";
}
function reset() {
  editing.value = null;
  name.value = "";
  url.value = "";
  color.value = "#6b9bd2";
  interval.value = 360;
}
async function work(action: () => Promise<unknown>, message: string) {
  if (busy.value) return false;
  const epoch = accountEpoch;
  busy.value = true;
  error.value = "";
  notice.value = "";
  try {
    await action();
    if (epoch !== accountEpoch) return false;
    notice.value = message;
    await load();
    return epoch === accountEpoch;
  } catch (e) {
    if (epoch !== accountEpoch) return false;
    error.value = e instanceof Error ? e.message : "Calendar request failed.";
    return false;
  } finally {
    if (epoch === accountEpoch) busy.value = false;
  }
}
async function save() {
  const saved = await work(async () => {
    const input = {
      name: name.value,
      color: color.value,
      refresh_minutes: interval.value,
    };
    if (editing.value)
      await updateCalendarSource(editing.value, {
        ...input,
        ...(url.value ? { url: url.value } : {}),
      });
    else
      await createCalendarSource({ ...input, url: url.value, enabled: true });
  }, "Subscription saved. It will appear on your calendar.");
  if (saved) reset();
}
async function remove(source: CalendarSource) {
  if (
    !(await confirm({
      title: "Delete subscription",
      message: `Remove ${source.name}, its imported events and local overrides? Manual copies are kept.`,
      confirmLabel: "Delete",
      danger: true,
    }))
  )
    return;
  const removed = await work(
    () => deleteCalendarSource(source.id),
    "Subscription deleted.",
  );
  if (removed && editing.value === source.id) reset();
}
const timestamp = (value: number | null) =>
  value ? new Date(value * 1000).toLocaleString() : "Never";
</script>

<template>
  <section class="calendar-sources">
    <h3>External calendars</h3>
    <p class="section-hint">
      Subscribe to an HTTPS ICS calendar. Your secret link is encrypted and
      never shown again. Imported events stay out of your published feed.
    </p>
    <p v-if="!remoteEnabled" role="status">
      Remote subscriptions are disabled by this deployment. Cached events and
      local edits are kept.
    </p>
    <p v-if="error" role="alert" class="error">
      {{ error }}
      <button
        type="button"
        class="ui-btn ui-btn-secondary"
        :disabled="busy"
        @click="load"
      >
        Try again
      </button>
    </p>
    <p v-if="notice" role="status">{{ notice }}</p>
    <p v-if="!loaded && !error" role="status">Loading subscriptions…</p>
    <p v-else-if="loaded && !sources.length">No external calendars yet.</p>
    <article v-for="source in sources" :key="source.id" class="source-row">
      <div>
        <h4>
          <span
            class="source-color"
            :style="{ background: source.color }"
          ></span
          >{{ source.name }}
        </h4>
        <p>{{ source.hostname }} · {{ sourceStatus(source) }}</p>
        <p class="section-hint">
          Last success: {{ timestamp(source.last_success_at) }} · Every
          {{ source.refresh_minutes }} minutes
        </p>
        <p v-if="source.warnings.length" role="status">
          Notices: {{ source.warnings.join(", ") }}
        </p>
      </div>
      <div class="source-actions">
        <button
          type="button"
          class="ui-btn ui-btn-secondary"
          :disabled="busy || !remoteEnabled || !source.enabled"
          @click="
            work(() => refreshCalendarSource(source.id), 'Refresh finished.')
          "
        >
          Refresh {{ source.name }}
        </button>
        <button
          type="button"
          class="ui-btn ui-btn-secondary"
          :disabled="busy"
          @click="edit(source)"
        >
          Edit {{ source.name }}
        </button>
        <button
          type="button"
          class="ui-btn ui-btn-secondary"
          :disabled="busy"
          @click="
            work(
              () =>
                updateCalendarSource(source.id, { enabled: !source.enabled }),
              source.enabled
                ? 'Subscription disabled; events are hidden.'
                : 'Subscription enabled.',
            )
          "
        >
          {{ source.enabled ? "Disable" : "Enable" }} {{ source.name }}
        </button>
        <button
          type="button"
          class="ui-btn ui-btn-secondary"
          :disabled="busy"
          @click="remove(source)"
        >
          Delete {{ source.name }}
        </button>
      </div>
    </article>
    <form v-if="loaded" class="source-form" @submit.prevent="save">
      <h4>{{ editing ? "Edit subscription" : "Add subscription" }}</h4>
      <label
        >Name<input
          v-model="name"
          name="calendar_subscription_name"
          autocomplete="off"
          class="ui-field"
          required
          maxlength="100"
          :disabled="busy"
      /></label>
      <label
        >{{
          editing
            ? "Replacement secret URL (leave blank to keep)"
            : "Secret ICS URL"
        }}<input
          v-model="url"
          class="ui-field"
          type="password"
          name="calendar_subscription_url"
          autocomplete="new-password"
          :required="!editing"
          maxlength="8192"
          :disabled="busy || !remoteEnabled"
      /></label>
      <p v-if="editing" class="section-hint">
        Replacing the URL resets imported events and overrides for this source
        after the new feed is validated.
      </p>
      <label
        >Colour<input v-model="color" type="color" :disabled="busy"
      /></label>
      <label
        >Refresh every (minutes)<input
          v-model.number="interval"
          class="ui-field"
          type="number"
          min="60"
          max="1440"
          required
          :disabled="busy"
      /></label>
      <div class="source-actions">
        <button
          type="submit"
          class="ui-btn ui-btn-primary"
          :disabled="
            busy || (!editing && (!remoteEnabled || sources.length >= 10))
          "
        >
          {{
            busy ? "Working…" : editing ? "Save subscription" : "Subscribe"
          }}</button
        ><button
          v-if="editing"
          type="button"
          class="ui-btn ui-btn-secondary"
          :disabled="busy"
          @click="reset"
        >
          Cancel editing
        </button>
      </div>
      <p class="section-hint">
        Up to 10 sources. Feeds are limited to 2 MiB / 10,000 components. Manual
        refresh is available every five minutes.
      </p>
    </form>
  </section>
</template>

<style scoped>
.calendar-sources {
  margin-top: 28px;
  border-top: 1px solid var(--ui-border);
  padding-top: 20px;
}
.source-row {
  padding: 16px 0;
  border-bottom: 1px solid var(--ui-border);
  overflow-wrap: anywhere;
}
.source-row h4 {
  margin: 0 0 8px;
}
.source-row p {
  margin: 6px 0;
}
.source-color {
  display: inline-block;
  width: 12px;
  height: 12px;
  border-radius: 50%;
  margin-right: 8px;
}
.source-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 12px;
}
.source-form {
  display: grid;
  gap: 12px;
  margin-top: 20px;
  max-width: 600px;
}
.source-form label {
  display: grid;
  gap: 6px;
}
.source-form input {
  min-width: 0;
  max-width: 100%;
}
.error {
  color: var(--ui-danger);
}
</style>
