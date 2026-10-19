<script setup lang="ts">
import { computed, ref } from "vue";
import UiModal from "./UiModal.vue";
import {
  clearCalendarOverride,
  copyExternalEvent,
  occurrenceTarget,
  setCalendarOverride,
  type ExternalCalendarEvent,
  type OverrideInput,
} from "../services/calendarSources";

const props = defineProps<{ event: ExternalCalendarEvent }>();
const emit = defineEmits<{ close: []; changed: [] }>();
const initial = props.event;
const title = ref(initial.title);
const note = ref(initial.note);
const location = ref(initial.location);
const date = ref(initial.event_date);
const time = ref(initial.event_time ?? "");
const allDay = initial.start.kind === "date";
const durationUnit = allDay ? 86400 : 60;
const duration = ref(initial.duration_seconds / durationUnit);
const scope = ref<"occurrence" | "series">("occurrence");
const error = ref("");
const busy = ref(false);
const hasOverride = computed(() =>
  scope.value === "series"
    ? initial.series_override
    : initial.occurrence_override,
);
function payload(): OverrideInput {
  const fields: OverrideInput["fields"] = {};
  if (title.value !== initial.title) fields.title = title.value;
  if (note.value !== initial.note) fields.note = note.value;
  if (location.value !== initial.location) fields.location = location.value;
  if (duration.value * durationUnit !== initial.duration_seconds)
    fields.duration_seconds = Math.round(duration.value * durationUnit);
  if (
    date.value !== initial.event_date ||
    time.value !== (initial.event_time ?? "")
  ) {
    fields.start = allDay
      ? { kind: "date", value: date.value }
      : initial.start.kind === "floating"
        ? { kind: "floating", value: `${date.value}T${time.value}:00` }
        : {
            kind: "zoned",
            value: `${date.value}T${time.value}:00`,
            tzid: Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC",
          };
  }
  return { ...occurrenceTarget(initial), scope: scope.value, fields };
}
async function work(action: () => Promise<unknown>) {
  if (busy.value) return;
  busy.value = true;
  error.value = "";
  try {
    await action();
    emit("changed");
    emit("close");
  } catch (e) {
    error.value = e instanceof Error ? e.message : "Could not save the event.";
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <UiModal
    title="External calendar event"
    :dismissible="!busy"
    @close="emit('close')"
  >
    <form
      class="external-event-form"
      @submit.prevent="
        work(() => setCalendarOverride(event.source.id, payload()))
      "
    >
      <p>
        <strong>{{ event.source.name }}</strong> · {{ event.source.hostname }}
      </p>
      <p class="section-hint">
        Local changes survive refresh. Upstream cancellation or removal still
        hides this event. Copy it to a manual entry to keep it independently.
      </p>
      <p v-if="error" class="error" role="alert">{{ error }}</p>
      <label v-if="event.recurring"
        >Apply changes to<select
          v-model="scope"
          class="ui-field"
          :disabled="busy"
        >
          <option value="occurrence">This occurrence</option>
          <option value="series">Whole series</option>
        </select></label
      >
      <p v-if="scope === 'series'" class="section-hint">
        A date/time change shifts every occurrence by the same wall-clock
        amount. Individual overrides keep precedence.
      </p>
      <label
        >Title<input
          v-model="title"
          class="ui-field"
          required
          maxlength="200"
          :disabled="busy"
      /></label>
      <div class="date-fields">
        <label
          >Date<input
            v-model="date"
            class="ui-field"
            type="date"
            required
            :disabled="busy" /></label
        ><label v-if="!allDay"
          >Time<input
            v-model="time"
            class="ui-field"
            type="time"
            required
            :disabled="busy"
        /></label>
      </div>
      <p v-if="!allDay" class="section-hint">
        {{
          initial.start.kind === "floating"
            ? "Floating time, shown as written."
            : `Times shown in ${Intl.DateTimeFormat().resolvedOptions().timeZone}.`
        }}
      </p>
      <label
        >Duration ({{ allDay ? "days" : "minutes" }})<input
          v-model.number="duration"
          class="ui-field"
          type="number"
          :min="allDay ? 1 : 0"
          :max="allDay ? 366 : 527040"
          step="1"
          required
          :disabled="busy"
      /></label>
      <label
        >Location<input
          v-model="location"
          class="ui-field"
          maxlength="500"
          :disabled="busy"
      /></label>
      <label
        >Notes<textarea
          v-model="note"
          class="ui-field"
          rows="4"
          maxlength="2000"
          :disabled="busy"
        ></textarea>
      </label>
      <a
        v-if="event.url"
        :href="event.url"
        target="_blank"
        rel="noopener noreferrer"
        >Open event website</a
      >
      <div class="actions">
        <button class="ui-btn ui-btn-primary" type="submit" :disabled="busy">
          {{ busy ? "Working…" : "Save local changes" }}</button
        ><button
          class="ui-btn ui-btn-secondary"
          type="button"
          :disabled="busy || !hasOverride"
          @click="work(() => clearCalendarOverride(event.source.id, payload()))"
        >
          Reset
          {{ scope === "series" ? "series" : "occurrence" }} overrides</button
        ><button
          class="ui-btn ui-btn-secondary"
          type="button"
          :disabled="busy"
          @click="
            work(() =>
              copyExternalEvent(event.source.id, occurrenceTarget(event)),
            )
          "
        >
          Copy to manual entry
        </button>
      </div>
    </form>
  </UiModal>
</template>

<style scoped>
.external-event-form {
  display: grid;
  gap: 12px;
}
.external-event-form label {
  display: grid;
  gap: 6px;
  min-width: 0;
}
.external-event-form input,
.external-event-form textarea {
  min-width: 0;
  max-width: 100%;
}
.date-fields {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}
.actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.error {
  color: var(--ui-danger);
}
</style>
