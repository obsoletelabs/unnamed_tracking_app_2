<script setup lang="ts">
// Notification preferences. These are stored on the server
// (not in this browser), so they follow you between devices, and each
// change saves as soon as it is made.
import { ref, computed, onMounted } from "vue";
import SegmentedControl from "./SegmentedControl.vue";
import NotificationRoutingSection from "./NotificationRoutingSection.vue";
import {
  DEFAULT_PREFERENCES,
  fetchPreferences,
  queuePreferences,
} from "../../services/preferences";
import type { Preferences } from "../../services/preferences";
import { preferences as sharedPreferences } from "../../state/preferences";
import { fetchInboxRetentionPolicy } from "../../services/notifications";
import type { InboxRetentionPolicy } from "../../services/notifications";

const prefs = ref<Preferences>({ ...DEFAULT_PREFERENCES });
const loaded = ref(false);
const error = ref<string | null>(null);
const savedNote = ref("");
const retention = ref<InboxRetentionPolicy | null>(null);

onMounted(async () => {
  try {
    prefs.value = await fetchPreferences();
    retention.value = await fetchInboxRetentionPolicy();
  } catch (e) {
    error.value = e instanceof Error ? e.message : "Failed to load settings.";
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
      retention.value = await fetchInboxRetentionPolicy();
    }
    savedNote.value = "Saved";
    setTimeout(() => (savedNote.value = ""), 1500);
  } catch (e) {
    error.value = e instanceof Error ? e.message : "Failed to save.";
    try {
      prefs.value = await fetchPreferences();
    } catch {
      prefs.value = previous;
    }
  }
}

const durationOptions = [
  { value: "7", label: "7 days" },
  { value: "14", label: "14 days" },
  { value: "30", label: "30 days" },
  { value: "90", label: "90 days" },
  { value: "180", label: "6 months" },
  { value: "365", label: "1 year" },
  { value: "0", label: "Unlimited" },
];
const retentionOptions = computed(() => [
  { value: "inherit", label: "Server default" },
  ...durationOptions.filter(
    (option) =>
      !retention.value?.maximum_days ||
      (Number(option.value) > 0 &&
        Number(option.value) <= retention.value.maximum_days) ||
      (!prefs.value.notification_retention_inherit &&
        Number(option.value) === prefs.value.notification_retention_days),
  ),
]);
function chooseRetention(value: string) {
  void change(
    value === "inherit"
      ? { notification_retention_inherit: true }
      : {
          notification_retention_inherit: false,
          notification_retention_days: Number(
            value,
          ) as Preferences["notification_retention_days"],
        },
  );
}
</script>

<template>
  <section class="settings-section">
    <h2>Notifications</h2>
    <p class="section-hint">
      Choose your providers, then the notifications they receive. Changes save
      to your account.
      <span v-if="savedNote" class="saved">{{ savedNote }}</span>
    </p>
    <p v-if="error" class="error" role="alert">{{ error }}</p>

    <NotificationRoutingSection
      :prefs="prefs"
      :loaded="loaded"
      @change="change"
    />
    <h3>Inbox history</h3>

    <p class="section-hint">
      Keep your delivered notices in the notification centre. The bell shows
      unread notices on every page.
    </p>
    <div class="field">
      <span>Keep inbox history</span>
      <SegmentedControl
        :model-value="
          prefs.notification_retention_inherit
            ? 'inherit'
            : String(prefs.notification_retention_days)
        "
        :options="retentionOptions"
        @update:model-value="chooseRetention"
      />
    </div>
    <p v-if="retention" class="section-hint" role="status">
      Your inbox keeps
      {{
        retention.effective_days
          ? `${retention.effective_days} days`
          : "unlimited"
      }}
      of history. Server default: {{ retention.default_days || "unlimited"
      }}{{ retention.default_days ? " days" : "" }}.<template
        v-if="retention.maximum_days"
      >
        Server maximum: {{ retention.maximum_days }} days.</template
      ><strong v-if="retention.limited">
        Your saved choice is limited by the server maximum; it has not been
        overwritten.</strong
      >
    </p>
    <p class="section-hint">
      Dismiss hides a notice from the inbox. Delete also cancels unsent
      deliveries. Browser notifications are separate from this saved history.
    </p>
  </section>
</template>

<style scoped>
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
