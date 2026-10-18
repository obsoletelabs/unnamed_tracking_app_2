<script setup lang="ts">
// Calendar preferences. These are stored on the server
// (not in this browser), so they follow you between devices, and each
// change saves as soon as it is made.
import { computed, ref, onMounted } from "vue";
import SegmentedControl from "./SegmentedControl.vue";
import ToggleButton from "./ToggleButton.vue";
import {
  DEFAULT_PREFERENCES,
  fetchPreferences,
  queuePreferences,
} from "../../services/preferences";
import type { Preferences } from "../../services/preferences";
import {
  calendarGameUnavailable,
  unavailableText,
} from "../../services/preferenceDependencies";
import { preferences as sharedPreferences } from "../../state/preferences";
import { WEEKDAY_NAMES } from "../../utils/calendarWeeks";

const prefs = ref<Preferences>({ ...DEFAULT_PREFERENCES });
const loaded = ref(false);
const error = ref<string | null>(null);
const savedNote = ref("");

onMounted(async () => {
  try {
    prefs.value = await fetchPreferences();
  } catch (e) {
    error.value =
      e instanceof Error ? e.message : "Could not load the settings.";
  } finally {
    loaded.value = true;
  }
});

async function change(changes: Partial<Preferences>) {
  const previous = { ...prefs.value };
  prefs.value = { ...prefs.value, ...changes };
  error.value = null;
  try {
    const { prefs: saved, latest } = await queuePreferences(changes);
    if (latest) {
      prefs.value = saved;
      sharedPreferences.value = saved;
    }
    savedNote.value = "Saved";
    setTimeout(() => (savedNote.value = ""), 1500);
  } catch (e) {
    error.value =
      e instanceof Error ? e.message : "Could not save the settings.";
    try {
      prefs.value = await fetchPreferences();
    } catch {
      prefs.value = previous;
    }
  }
}

type NotifyStatus = Preferences["notify_statuses"][number];
// switch one member of a set-valued preference on or off
function setMember<T extends string>(list: T[], value: T, on: boolean): T[] {
  return on
    ? [...list.filter((v) => v !== value), value]
    : list.filter((v) => v !== value);
}
const gameUnavailable = computed(() => calendarGameUnavailable(prefs.value));
const NOTIFY_STATUS_ROWS: { key: NotifyStatus; label: string; hint: string }[] =
  [
    {
      key: "watching",
      label: "Watching",
      hint: "titles you are in the middle of",
    },
    {
      key: "plan",
      label: "Plan to Watch",
      hint: "titles you have not started",
    },
    { key: "hold", label: "On Hold", hint: "titles you put aside" },
  ];
const viewOptions = [
  { value: "month", label: "Month" },
  { value: "week", label: "Week" },
  { value: "agenda", label: "Agenda" },
];
</script>

<template>
  <section class="settings-section">
    <h2>Calendar</h2>
    <p class="section-hint">
      Saved on the server, so they follow you between browsers.
      <span v-if="savedNote" class="saved">{{ savedNote }}</span>
    </p>
    <p v-if="error" class="error">{{ error }}</p>

    <ToggleButton
      :model-value="prefs.calendar_game_releases"
      label="Game releases"
      :disabled="!loaded || !!gameUnavailable"
      :aria-describedby="gameUnavailable ? 'calendar-games-reason' : undefined"
      @update:model-value="change({ calendar_game_releases: $event })"
    >
      <strong>Game releases</strong>: show the release date of every game in
      your library, past and upcoming
    </ToggleButton>
    <ToggleButton
      :model-value="prefs.calendar_game_history"
      label="Games history"
      :disabled="!loaded || !!gameUnavailable"
      :aria-describedby="gameUnavailable ? 'calendar-games-reason' : undefined"
      @update:model-value="change({ calendar_game_history: $event })"
    >
      <strong>Games history</strong>: show the day you finished a game and the
      days you unlocked achievements
    </ToggleButton>

    <p
      v-if="gameUnavailable"
      id="calendar-games-reason"
      class="restriction"
      role="status"
    >
      {{ unavailableText(gameUnavailable) }}
    </p>
    <ToggleButton
      :model-value="prefs.calendar_show_estimated"
      label="Estimated episodes"
      :disabled="!loaded"
      @update:model-value="change({ calendar_show_estimated: $event })"
    >
      <strong>Estimated episodes</strong>: show later episodes projected from
      the show's usual schedule (drawn dashed). Only the next episode of a show
      is a confirmed date.
    </ToggleButton>
    <ToggleButton
      :model-value="prefs.calendar_hide_games"
      label="Hide all games layers"
      :disabled="!loaded"
      @update:model-value="change({ calendar_hide_games: $event })"
    >
      <strong>Hide all games layers</strong>: keep games off the calendar even
      when the two options above are on
    </ToggleButton>

    <h4 class="scope-title">Show airing episodes for titles that are</h4>
    <ToggleButton
      v-for="row in NOTIFY_STATUS_ROWS"
      :key="row.key"
      :model-value="prefs.calendar_airing_statuses.includes(row.key)"
      :label="row.label"
      :disabled="!loaded"
      @update:model-value="
        change({
          calendar_airing_statuses: setMember(
            prefs.calendar_airing_statuses,
            row.key,
            $event,
          ),
        })
      "
    >
      <strong>{{ row.label }}</strong
      >: {{ row.hint }}
    </ToggleButton>

    <div class="field">
      <span>Opens in</span>
      <SegmentedControl
        :model-value="prefs.calendar_default_view"
        :options="viewOptions"
        @update:model-value="
          change({
            calendar_default_view:
              $event as Preferences['calendar_default_view'],
          })
        "
      />
    </div>
    <div class="field">
      <label for="calendar-week-start">Week starts on</label>
      <select
        id="calendar-week-start"
        class="week-start-select"
        :value="String(prefs.calendar_week_start)"
        :disabled="!loaded"
        @change="
          change({
            calendar_week_start: Number(
              ($event.target as HTMLSelectElement).value,
            ) as Preferences['calendar_week_start'],
          })
        "
      >
        <option
          v-for="(name, index) in WEEKDAY_NAMES"
          :key="name"
          :value="String(index)"
        >
          {{ name }}
        </option>
      </select>
    </div>
  </section>
</template>

<style scoped>
.week-start-select {
  align-self: flex-start;
  min-height: var(--ui-control-height);
  background: var(--ui-bg);
  border: 1px solid var(--ui-border-strong);
  border-radius: var(--ui-radius-control);
  color: var(--ui-text);
  padding: 10px 12px;
  font: inherit;
}
.settings-section h2 {
  margin: 0 0 12px;
  font: var(--ui-weight-heading) var(--ui-font-heading)/1.4
    var(--ui-font-family);
  color: var(--ui-text);
}
.settings-section h3 {
  margin: 22px 0 8px;
  font-size: 0.86rem;
  color: var(--ui-text);
}
.scope-title {
  margin: 18px 0 8px;
  font-size: 0.78rem;
  font-weight: 700;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  color: #b9b9b9;
}
.scope-note {
  margin-top: 10px;
}
.section-hint {
  color: var(--ui-dim);
  font-size: 0.82rem;
  line-height: 1.6;
  margin: 0 0 14px;
}
.saved {
  color: var(--ui-good);
  margin-left: 8px;
  font-weight: 700;
}
.restriction {
  margin: -6px 0 12px 44px;
  color: var(--ui-dim);
  font-size: 0.78rem;
  line-height: 1.5;
}
.error {
  color: var(--ui-error);
  font-size: 0.82rem;
}
.field {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin: 14px 0;
  font-size: 0.82rem;
  color: var(--ui-text);
}
.settings-section :deep(.toggle-button) {
  margin: 12px 0;
}
</style>
