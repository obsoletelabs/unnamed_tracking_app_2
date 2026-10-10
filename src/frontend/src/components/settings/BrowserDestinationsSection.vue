<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";
import ToggleButton from "./ToggleButton.vue";
import {
  browserPushSupported,
  enableBrowserPush,
} from "../../services/notificationBrowser";
import { unsubscribeBrowserPush } from "../../services/pwa";
import {
  fetchBrowserPushConfiguration,
  removeBrowserDestination,
  updateBrowserDestination,
  sendDestinationTest,
  type BrowserPushConfiguration,
  type NotificationRoutingDestination,
  type NotificationRoutingProvider,
} from "../../services/notifications";
import type { Preferences } from "../../services/preferences";

defineProps<{
  provider: NotificationRoutingProvider;
  destinations: NotificationRoutingDestination[];
  prefs: Preferences;
  loaded: boolean;
}>();
const emit = defineEmits<{
  changed: [];
  change: [changes: Partial<Preferences>];
}>();
const configuration = ref<BrowserPushConfiguration | null>(null);
const label = ref("This browser");
const busy = ref(false);
const error = ref("");
const status = ref("");
const permission = ref(
  browserPushSupported() ? Notification.permission : "unsupported",
);
const current = computed(() => configuration.value?.current_destination_id);
async function refresh() {
  try {
    configuration.value = await fetchBrowserPushConfiguration();
  } catch (reason) {
    error.value =
      reason instanceof Error
        ? reason.message
        : "Could not load browser delivery.";
  }
}
async function perform(operation: () => Promise<unknown>, message: string) {
  busy.value = true;
  error.value = "";
  status.value = "";
  try {
    await operation();
    status.value = message;
    await refresh();
    emit("changed");
  } catch (reason) {
    error.value =
      reason instanceof Error
        ? reason.message
        : "Could not update browser delivery.";
  } finally {
    busy.value = false;
    permission.value = browserPushSupported()
      ? Notification.permission
      : "unsupported";
  }
}
async function add() {
  const config = configuration.value;
  if (!config) return;
  await perform(
    () => enableBrowserPush(config, label.value),
    "This browser is enrolled. Use Send test to check delivery.",
  );
}
async function remove(destination: NotificationRoutingDestination) {
  await perform(async () => {
    await removeBrowserDestination(destination.id);
    if (destination.id === current.value)
      await unsubscribeBrowserPush().catch(() => {});
  }, "Browser destination removed. Pending deliveries were cancelled.");
}
function subscriptionChanged(event: MessageEvent) {
  if (event.data?.type === "tracking-push-subscription-expired") {
    void withdrawCurrent(
      "Your browser subscription changed. Enroll this browser again to resume delivery.",
    );
  }
}
async function withdrawCurrent(message: string) {
  if (busy.value) return;
  const id = current.value;
  await perform(async () => {
    if (id) await removeBrowserDestination(id);
    await unsubscribeBrowserPush().catch(() => {});
  }, message);
}
function permissionChanged() {
  permission.value = browserPushSupported()
    ? Notification.permission
    : "unsupported";
  if (permission.value === "denied" && current.value)
    void withdrawCurrent(
      "Browser permission was revoked. Your destination was removed.",
    );
}
onMounted(() => {
  void refresh().then(permissionChanged);
  navigator.serviceWorker?.addEventListener("message", subscriptionChanged);
  window.addEventListener("focus", permissionChanged);
});
onUnmounted(() => {
  navigator.serviceWorker?.removeEventListener("message", subscriptionChanged);
  window.removeEventListener("focus", permissionChanged);
});
</script>

<template>
  <section class="browser-destinations" aria-label="My browser destinations">
    <p class="hint">
      Browser notifications are PRIVATE and show a generic preview. Open the app
      to read the notification. They use Normal urgency and cannot deliver
      password resets or other recovery secrets.
    </p>
    <p v-if="permission === 'unsupported'" class="hint">
      Use a supported browser over HTTPS (or localhost). On iPhone and iPad,
      install the app on the home screen first.
    </p>
    <p v-else-if="permission === 'denied'" class="hint">
      Notifications are blocked in this browser. Allow them in this site's
      browser permissions before enrolling again.
    </p>
    <p v-if="!provider.available" class="hint">
      An administrator must configure browser delivery and enable the PWA plugin
      with its permission.
    </p>
    <form @submit.prevent="add">
      <label
        >Browser label<input
          v-model="label"
          maxlength="80"
          :disabled="busy"
          placeholder="Home laptop"
      /></label>
      <button
        :disabled="
          busy ||
          !loaded ||
          permission === 'unsupported' ||
          permission === 'denied' ||
          !configuration?.enabled ||
          !configuration.session_authenticated
        "
      >
        {{ current ? "Enroll this browser again" : "Enable on this browser" }}
      </button>
    </form>
    <p class="hint">
      Enrollment belongs to this signed-in session. Signing out, changing
      accounts or replacing the server key requires fresh enrollment. Removing
      another device here cancels its app delivery.
    </p>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <p v-if="status" class="success" role="status">{{ status }}</p>
    <article
      v-for="destination in destinations.filter((d) => d.active)"
      :key="destination.id"
      class="browser-card"
    >
      <h5>
        {{ destination.label || "Browser"
        }}<span v-if="destination.id === current"> · This session</span>
      </h5>
      <p class="hint">
        PRIVATE · External ·
        {{
          destination.available
            ? "Session active"
            : "Unavailable — enroll that browser again"
        }}
      </p>
      <ToggleButton
        :model-value="
          destination.enabled &&
          prefs.notification_destinations[destination.id] !== false
        "
        :label="`Use ${destination.label || 'browser'}`"
        :disabled="busy || !loaded || !destination.available"
        @update:model-value="
          perform(async () => {
            await updateBrowserDestination(destination.id, { enabled: $event });
            emit('change', {
              notification_destinations: {
                ...prefs.notification_destinations,
                [destination.id]: $event,
              },
            });
          }, 'Browser routing saved.')
        "
        >Use this browser</ToggleButton
      >
      <div class="actions">
        <button
          :disabled="
            busy ||
            !destination.available ||
            !destination.enabled ||
            !destination.provider_enabled ||
            prefs.notification_destinations[destination.id] === false
          "
          @click="
            perform(
              () => sendDestinationTest(destination.id),
              'Test queued. Keep this browser signed in; delivery may take a minute.',
            )
          "
        >
          Send test
        </button>
        <button :disabled="busy" @click="remove(destination)">
          Remove browser
        </button>
      </div>
    </article>
    <details v-if="destinations.some((d) => !d.active)">
      <summary>Previous browser destinations</summary>
      <p
        v-for="destination in destinations.filter((d) => !d.active)"
        :key="destination.id"
        class="hint"
      >
        {{ destination.label || "Browser" }} · Inactive. History is retained;
        enroll again to resume.
      </p>
    </details>
  </section>
</template>

<style scoped>
.hint {
  color: var(--ui-dim);
  font-size: 0.8rem;
  line-height: 1.6;
}
.error {
  color: var(--ui-error);
}
.success {
  color: var(--ui-accent);
}
form,
.actions {
  display: flex;
  align-items: end;
  flex-wrap: wrap;
  gap: 10px;
  margin: 12px 0;
}
label {
  display: flex;
  flex-direction: column;
  gap: 6px;
  flex: 1;
  font-size: 0.8rem;
}
input {
  width: 100%;
  box-sizing: border-box;
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
  padding: 10px 14px;
  color: var(--ui-on-accent);
  background: var(--ui-accent);
  cursor: pointer;
}
button:disabled {
  opacity: 0.5;
  cursor: default;
}
.browser-card {
  margin: 12px 0;
  border: 1px solid var(--ui-border);
  padding: 12px;
  border-radius: 10px;
}
h5 {
  margin: 0;
  font-size: 0.85rem;
}
summary {
  cursor: pointer;
  font-size: 0.8rem;
}
@media (max-width: 520px) {
  form {
    flex-direction: column;
    align-items: stretch;
  }
}
</style>
