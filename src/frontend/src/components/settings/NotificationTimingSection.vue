<script setup lang="ts">
// Quiet hours, digest schedules, per-category/destination timing rules and
// urgency limits. They extend the routing choices above and are saved with the
// same preferences; they only change when a permitted notification is sent,
// never who may receive it or how much it may show.
import { computed, onMounted, ref } from "vue";
import ToggleButton from "./ToggleButton.vue";
import {
  fetchNotificationRouting,
  previewNotificationTiming,
} from "../../services/notifications";
import type {
  NotificationRoutingSettings,
  TimingPreview,
} from "../../services/notifications";
import { DEFAULT_NOTIFICATION_TIMING } from "../../services/preferences";
import type {
  NotificationTiming,
  Preferences,
  TimingCategory,
  TimingMode,
  TimingRule,
} from "../../services/preferences";

const props = defineProps<{ prefs: Preferences; loaded: boolean }>();
const emit = defineEmits<{ change: [changes: Partial<Preferences>] }>();

const timing = computed<NotificationTiming>(() => ({
  ...DEFAULT_NOTIFICATION_TIMING,
  ...props.prefs.notification_timing,
}));
const routing = ref<NotificationRoutingSettings | null>(null);
const preview = ref<TimingPreview | null>(null);
const previewType = ref("");
const previewDestination = ref("");
const previewUrgent = ref(false);
const previewError = ref("");

onMounted(async () => {
  try {
    routing.value = await fetchNotificationRouting();
    previewType.value = routing.value.types[0]?.event_type ?? "";
    previewDestination.value = routing.value.destinations[0]?.id ?? "";
  } catch {
    routing.value = null;
  }
});

const zones = computed(() => {
  const supported = (
    Intl as unknown as { supportedValuesOf?: (key: string) => string[] }
  ).supportedValuesOf;
  return supported ? supported("timeZone") : [];
});
const deviceZone = Intl.DateTimeFormat().resolvedOptions().timeZone;

const modes: { value: TimingMode; label: string }[] = [
  { value: "inherit", label: "Default for this type" },
  { value: "immediate", label: "Send immediately" },
  { value: "coalesce", label: "Group briefly, then send" },
  { value: "digest", label: "Daily digest" },
  { value: "mute", label: "Mute outside the inbox" },
];
const categories: { value: TimingCategory; label: string }[] = [
  { value: "*", label: "Everything" },
  { value: "media", label: "TV, anime and movies" },
  { value: "game", label: "Games" },
  { value: "system", label: "System updates" },
  { value: "plugin", label: "Plugins" },
];

function save(changes: Partial<NotificationTiming>) {
  preview.value = null;
  emit("change", { notification_timing: { ...timing.value, ...changes } });
}
function setQuiet(changes: Partial<NotificationTiming["quiet_hours"]>) {
  save({ quiet_hours: { ...timing.value.quiet_hours, ...changes } });
}
function setRule(index: number, changes: Partial<TimingRule>) {
  save({
    rules: timing.value.rules.map((rule, i) =>
      i === index ? { ...rule, ...changes } : rule,
    ),
  });
}
function addRule() {
  const used = new Set(
    timing.value.rules.map((rule) => `${rule.category}|${rule.destination_id}`),
  );
  const category = categories.find(
    (item) => !used.has(`${item.value}|null`),
  )?.value;
  if (!category) return;
  save({
    rules: [
      ...timing.value.rules,
      {
        category,
        destination_id: null,
        mode: "inherit",
        urgent: "inherit",
      },
    ],
  });
}
function removeRule(index: number) {
  save({ rules: timing.value.rules.filter((_, i) => i !== index) });
}
function ruleQuiet(rule: TimingRule): "inherit" | "off" | "custom" {
  if (!rule.quiet_hours || !Object.keys(rule.quiet_hours).length)
    return "inherit";
  return rule.quiet_hours.enabled === false ? "off" : "custom";
}
function setRuleQuiet(index: number, value: string) {
  setRule(index, {
    quiet_hours:
      value === "inherit"
        ? {}
        : value === "off"
          ? { enabled: false }
          : { enabled: true, start: "23:00", end: "06:00" },
  });
}
function setDigestTime(index: number, value: string) {
  if (!/^\d\d:\d\d$/.test(value)) return;
  save({
    digest_times: timing.value.digest_times.map((time, i) =>
      i === index ? value : time,
    ),
  });
}
function addDigestTime() {
  if (timing.value.digest_times.length >= 6) return;
  save({ digest_times: [...timing.value.digest_times, "18:00"] });
}
function removeDigestTime(index: number) {
  if (timing.value.digest_times.length <= 1) return;
  save({
    digest_times: timing.value.digest_times.filter((_, i) => i !== index),
  });
}
function destinationLabel(id: string) {
  const item = routing.value?.destinations.find((entry) => entry.id === id);
  if (!item) return "Unknown destination";
  return item.label
    ? `${item.provider_name} · ${item.label}`
    : item.provider_name;
}
async function runPreview() {
  previewError.value = "";
  preview.value = null;
  try {
    preview.value = await previewNotificationTiming(
      previewType.value,
      previewDestination.value,
      { urgent: previewUrgent.value },
    );
  } catch (reason) {
    previewError.value =
      reason instanceof Error ? reason.message : "Preview failed.";
  }
}
const actionText: Record<string, string> = {
  deliver: "Sent right away",
  hold: "Held until",
  aggregate: "Grouped, then sent",
  mute: "Not sent outside the inbox",
  none: "Nothing is queued",
};
function when(stamp: number | null) {
  return stamp
    ? new Intl.DateTimeFormat(undefined, {
        dateStyle: "medium",
        timeStyle: "short",
        timeZone: timing.value.timezone || "UTC",
      }).format(new Date(stamp * 1000))
    : "";
}
</script>

<template>
  <section class="timing" aria-labelledby="timing-heading">
    <h3 id="timing-heading">Quiet hours and timing</h3>
    <p class="section-hint">
      Choose when external notifications are sent. Security alerts, sign-in
      codes and the in-app inbox are never delayed, and timing never changes
      what a destination is allowed to see.
    </p>

    <div class="field">
      <label for="timing-zone">Time zone</label>
      <input
        id="timing-zone"
        list="timing-zones"
        :value="timing.timezone"
        :disabled="!loaded"
        placeholder="UTC"
        @change="
          save({ timezone: ($event.target as HTMLInputElement).value.trim() })
        "
      />
      <datalist id="timing-zones">
        <option v-for="zone in zones" :key="zone" :value="zone" />
      </datalist>
      <button
        v-if="deviceZone && deviceZone !== timing.timezone"
        type="button"
        class="link"
        @click="save({ timezone: deviceZone })"
      >
        Use this device ({{ deviceZone }})
      </button>
      <p class="hint">
        Quiet hours and digests follow this zone, including daylight saving
        changes. Leave empty for UTC.
      </p>
    </div>

    <ToggleButton
      label="Quiet hours"
      aria-label="Quiet hours"
      :model-value="timing.quiet_hours.enabled"
      :disabled="!loaded"
      @update:model-value="setQuiet({ enabled: $event })"
    >
      <strong>Hold external notifications during quiet hours</strong>
    </ToggleButton>
    <div class="row">
      <label>
        From
        <input
          type="time"
          :value="timing.quiet_hours.start"
          :disabled="!loaded || !timing.quiet_hours.enabled"
          aria-label="Quiet hours start"
          @change="
            setQuiet({ start: ($event.target as HTMLInputElement).value })
          "
        />
      </label>
      <label>
        Until
        <input
          type="time"
          :value="timing.quiet_hours.end"
          :disabled="!loaded || !timing.quiet_hours.enabled"
          aria-label="Quiet hours end"
          @change="setQuiet({ end: ($event.target as HTMLInputElement).value })"
        />
      </label>
    </div>
    <p class="hint">
      Held notifications arrive together as one message when quiet hours end.
    </p>

    <h4>Urgent requests</h4>
    <label class="field">
      <span>Urgent requests may interrupt quiet hours</span>
      <select
        :value="timing.urgent_threshold"
        :disabled="!loaded"
        aria-label="Urgent requests"
        @change="
          save({
            urgent_threshold: ($event.target as HTMLSelectElement).value as
              'critical' | 'off',
          })
        "
      >
        <option value="critical">Yes, for Critical routes and alerts</option>
        <option value="off">Never</option>
      </select>
    </label>
    <ToggleButton
      label="Let plugins request urgent delivery"
      aria-label="Let plugins request urgent delivery"
      :model-value="timing.plugin_urgent_bypass"
      :disabled="!loaded || timing.urgent_threshold === 'off'"
      @update:model-value="save({ plugin_urgent_bypass: $event })"
    >
      <strong>Honour a plugin's urgent request</strong>
    </ToggleButton>
    <p class="hint">
      Your server administrator can also turn urgent interruption off. An urgent
      request never widens who receives a notification or what it shows.
    </p>

    <h4>Digest times</h4>
    <div v-for="(time, index) in timing.digest_times" :key="index" class="row">
      <input
        type="time"
        :value="time"
        :disabled="!loaded"
        :aria-label="`Digest time ${index + 1}`"
        @change="
          setDigestTime(index, ($event.target as HTMLInputElement).value)
        "
      />
      <button
        type="button"
        class="link"
        :disabled="timing.digest_times.length <= 1"
        :aria-label="`Remove digest time ${index + 1}`"
        @click="removeDigestTime(index)"
      >
        Remove
      </button>
    </div>
    <button
      type="button"
      class="link"
      :disabled="!loaded || timing.digest_times.length >= 6"
      @click="addDigestTime"
    >
      Add digest time
    </button>

    <h4>Rules</h4>
    <p class="hint">
      The most specific rule wins: a category on one destination, then one
      destination, then a category, then everything. Disabled routes above stay
      disabled.
    </p>
    <div
      v-for="(rule, index) in timing.rules"
      :key="`${rule.category}-${rule.destination_id}`"
      class="rule"
    >
      <label>
        Applies to
        <select
          :value="rule.category"
          :aria-label="`Rule ${index + 1} category`"
          @change="
            setRule(index, {
              category: ($event.target as HTMLSelectElement)
                .value as TimingCategory,
            })
          "
        >
          <option
            v-for="item in categories"
            :key="item.value"
            :value="item.value"
          >
            {{ item.label }}
          </option>
        </select>
      </label>
      <label>
        Destination
        <select
          :value="rule.destination_id ?? ''"
          :aria-label="`Rule ${index + 1} destination`"
          @change="
            setRule(index, {
              destination_id:
                ($event.target as HTMLSelectElement).value || null,
            })
          "
        >
          <option value="">Every destination</option>
          <option
            v-for="item in routing?.destinations ?? []"
            :key="item.id"
            :value="item.id"
          >
            {{ destinationLabel(item.id) }}
          </option>
        </select>
      </label>
      <label>
        Send
        <select
          :value="rule.mode"
          :aria-label="`Rule ${index + 1} mode`"
          @change="
            setRule(index, {
              mode: ($event.target as HTMLSelectElement).value as TimingMode,
            })
          "
        >
          <option v-for="item in modes" :key="item.value" :value="item.value">
            {{ item.label }}
          </option>
        </select>
      </label>
      <label>
        Quiet hours
        <select
          :value="ruleQuiet(rule)"
          :aria-label="`Rule ${index + 1} quiet hours`"
          @change="
            setRuleQuiet(index, ($event.target as HTMLSelectElement).value)
          "
        >
          <option value="inherit">Use my quiet hours</option>
          <option value="off">Ignore quiet hours</option>
          <option value="custom">Custom hours</option>
        </select>
      </label>
      <div v-if="ruleQuiet(rule) === 'custom'" class="row">
        <input
          type="time"
          :value="rule.quiet_hours?.start"
          :aria-label="`Rule ${index + 1} quiet start`"
          @change="
            setRule(index, {
              quiet_hours: {
                ...rule.quiet_hours,
                start: ($event.target as HTMLInputElement).value,
              },
            })
          "
        />
        <input
          type="time"
          :value="rule.quiet_hours?.end"
          :aria-label="`Rule ${index + 1} quiet end`"
          @change="
            setRule(index, {
              quiet_hours: {
                ...rule.quiet_hours,
                end: ($event.target as HTMLInputElement).value,
              },
            })
          "
        />
      </div>
      <label>
        Urgent requests
        <select
          :value="rule.urgent"
          :aria-label="`Rule ${index + 1} urgent requests`"
          @change="
            setRule(index, {
              urgent: ($event.target as HTMLSelectElement).value as
                'inherit' | 'allow' | 'deny',
            })
          "
        >
          <option value="inherit">Follow my setting</option>
          <option value="allow">May interrupt</option>
          <option value="deny">Never interrupt</option>
        </select>
      </label>
      <button
        type="button"
        class="link"
        :aria-label="`Remove rule ${index + 1}`"
        @click="removeRule(index)"
      >
        Remove rule
      </button>
    </div>
    <button
      type="button"
      class="link"
      :disabled="!loaded || timing.rules.length >= 64"
      @click="addRule"
    >
      Add rule
    </button>

    <h4>Check what happens</h4>
    <div v-if="routing && routing.destinations.length" class="row">
      <label>
        Notification
        <select v-model="previewType" aria-label="Preview type">
          <option
            v-for="type in routing.types"
            :key="type.event_type"
            :value="type.event_type"
          >
            {{ type.label }}
          </option>
        </select>
      </label>
      <label>
        Destination
        <select v-model="previewDestination" aria-label="Preview destination">
          <option
            v-for="item in routing.destinations"
            :key="item.id"
            :value="item.id"
          >
            {{ destinationLabel(item.id) }}
          </option>
        </select>
      </label>
      <label class="inline">
        <input v-model="previewUrgent" type="checkbox" /> Urgent
      </label>
      <button type="button" class="link" @click="runPreview">Explain</button>
    </div>
    <p v-else class="hint">Add a destination above to preview its timing.</p>
    <p v-if="previewError" class="error" role="alert">{{ previewError }}</p>
    <div v-if="preview" class="preview" role="status">
      <strong>
        {{ actionText[preview.decision.action] }}
        <template v-if="preview.decision.release_at">
          {{ when(preview.decision.release_at) }}
        </template>
      </strong>
      <ul>
        <li v-for="reason in preview.decision.reasons" :key="reason">
          {{ reason }}
        </li>
      </ul>
    </div>
  </section>
</template>

<style scoped>
.timing {
  margin-top: 22px;
}
h3 {
  margin: 22px 0 8px;
  font-size: 0.95rem;
}
h4 {
  margin: 18px 0 8px;
  font-size: 0.85rem;
}
.field {
  display: flex;
  flex-direction: column;
  gap: 5px;
  margin: 10px 0;
}
.row {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  gap: 12px;
  margin: 8px 0;
}
.rule {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
  gap: 10px;
  align-items: end;
  border: 1px solid var(--ui-border);
  border-radius: 10px;
  padding: 12px;
  margin: 10px 0;
  background: var(--ui-surface);
}
label {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 0.78rem;
}
label.inline {
  flex-direction: row;
  align-items: center;
}
select,
input:not([type="checkbox"]) {
  color: var(--ui-text);
  background: var(--ui-surface-alt);
  border: 1px solid var(--ui-border);
  border-radius: 6px;
  padding: 7px;
  font: inherit;
  min-width: 0;
}
.link {
  align-self: flex-start;
  border: 1px solid var(--ui-border);
  border-radius: 6px;
  padding: 6px 10px;
  color: var(--ui-text);
  background: var(--ui-surface-alt);
  cursor: pointer;
}
.link:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.preview {
  border: 1px solid var(--ui-border);
  border-radius: 10px;
  padding: 12px;
  background: var(--ui-surface);
}
.preview ul {
  margin: 8px 0 0;
  padding-left: 18px;
  font-size: 0.82rem;
}
.error {
  color: var(--ui-error);
}
</style>
