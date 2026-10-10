<script setup lang="ts">
import { reactive, ref, watch } from "vue";
import { setDestinationNotificationUrl } from "../../services/notifications";
import type { NotificationRoutingSettings } from "../../services/notifications";
import type { Preferences } from "../../services/preferences";

const props = defineProps<{
  routing: NotificationRoutingSettings;
  prefs: Preferences;
  loaded: boolean;
}>();
const emit = defineEmits<{
  change: [changes: Partial<Preferences>];
  changed: [];
}>();
const personal = ref("");
const urls = reactive<Record<string, string>>({});
const busy = ref(false);
const error = ref("");
watch(
  () => props.prefs.notification_url,
  (value) => {
    personal.value = value;
  },
  { immediate: true },
);
watch(
  () => props.routing,
  (value) => {
    for (const destination of value.destinations)
      urls[destination.id] = destination.notification_url ?? "";
  },
  { immediate: true },
);
async function saveDestination(id: string) {
  busy.value = true;
  error.value = "";
  try {
    await setDestinationNotificationUrl(id, urls[id] ?? "");
    emit("changed");
  } catch (reason) {
    error.value =
      reason instanceof Error
        ? reason.message
        : "Could not save notification URL.";
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <section class="link-settings" aria-label="Notification links">
    <h3>Notification links</h3>
    <p class="hint">
      Choose where notification links open. Leave blank to use the shared public
      app URL or your last-used app URL. These choices do not change sign-in or
      OIDC redirects. Browser push always opens the inbox on the origin where
      that browser was enrolled.
    </p>
    <form @submit.prevent="emit('change', { notification_url: personal })">
      <label
        >My notification URL<input
          v-model="personal"
          type="url"
          maxlength="2048"
          :placeholder="routing.default_url || 'Use my last-used app URL'"
          :disabled="!loaded || busy"
      /></label>
      <button :disabled="!loaded || busy">Save notification URL</button>
    </form>
    <p class="hint">
      Current default:
      {{
        routing.default_url || "Visit the app while signed in to detect a URL."
      }}
    </p>
    <details>
      <summary>Different URLs for individual destinations</summary>
      <p class="hint">
        For example, an email can open your public domain while a Discord link
        opens another domain. Blank destinations inherit your notification URL.
        A URL never changes destination trust.
      </p>
      <form
        v-for="destination in routing.destinations.filter(
          (d) =>
            d.active && d.context === 'external' && d.kind !== 'browser_push',
        )"
        :key="destination.id"
        @submit.prevent="saveDestination(destination.id)"
      >
        <label
          >{{ destination.provider_name }} ·
          {{ destination.label || destination.kind
          }}<input
            v-model="urls[destination.id]"
            type="url"
            maxlength="2048"
            :placeholder="
              routing.default_url || 'Use my default notification URL'
            "
            :disabled="busy"
        /></label>
        <button :disabled="busy">Save destination URL</button>
      </form>
    </details>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
  </section>
</template>

<style scoped>
.link-settings {
  margin: 22px 0;
  border: 1px solid var(--ui-border);
  border-radius: 10px;
  padding: 14px;
}
h3 {
  margin: 0 0 8px;
  font-size: 0.95rem;
}
.hint {
  color: var(--ui-dim);
  font-size: 0.78rem;
  line-height: 1.6;
}
form {
  display: flex;
  align-items: end;
  gap: 10px;
  margin: 12px 0;
}
label {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 0.8rem;
}
input {
  min-width: 0;
  color: var(--ui-text);
  background: var(--ui-bg);
  border: 1px solid var(--ui-border-strong);
  border-radius: var(--ui-radius-control);
  padding: 10px;
  font: inherit;
}
button {
  border: 0;
  border-radius: var(--ui-radius-control);
  background: var(--ui-accent);
  color: var(--ui-on-accent);
  padding: 10px 14px;
  cursor: pointer;
}
button:disabled {
  opacity: 0.55;
}
summary {
  cursor: pointer;
  font-size: 0.82rem;
}
.error {
  color: var(--ui-error);
}
@media (max-width: 760px) {
  form {
    align-items: stretch;
    flex-direction: column;
  }
}
</style>
