<script setup lang="ts">
import { computed, reactive, ref } from "vue";
import ToggleButton from "./ToggleButton.vue";
import {
  createWebhookDestination,
  updateWebhookDestination,
  removeWebhookDestination,
  sendDestinationTest,
} from "../../services/notifications";
import type {
  NotificationRoutingDestination,
  NotificationRoutingProvider,
} from "../../services/notifications";
import type { Preferences } from "../../services/preferences";

const props = defineProps<{
  provider: NotificationRoutingProvider;
  destinations: NotificationRoutingDestination[];
  prefs: Preferences;
  loaded: boolean;
}>();
const emit = defineEmits<{
  changed: [];
  change: [changes: Partial<Preferences>];
}>();
const url = ref("");
const label = ref("");
const error = ref("");
const status = ref("");
const busy = ref(false);
const confirming = ref<string | null>(null);
const removing = ref<string | null>(null);
const edits = reactive<Record<string, string>>({});
const showInactive = ref(false);
const inactiveCount = computed(
  () => props.destinations.filter((d) => !d.active).length,
);
const listedDestinations = computed(() =>
  props.destinations
    .filter((d) => d.active || showInactive.value)
    .sort((a, b) => Number(b.active) - Number(a.active)),
);

async function perform(operation: () => Promise<unknown>, success: string) {
  busy.value = true;
  error.value = "";
  status.value = "";
  try {
    await operation();
    status.value = success;
    emit("changed");
  } catch (reason) {
    error.value =
      reason instanceof Error ? reason.message : "Could not save webhook.";
  } finally {
    busy.value = false;
  }
}
async function add() {
  await perform(async () => {
    await createWebhookDestination(props.provider.id, url.value, label.value);
    url.value = "";
    label.value = "";
  }, "Webhook added with public release announcements. Choose its notification types below.");
}
async function use(
  destination: NotificationRoutingDestination,
  enabled: boolean,
) {
  await perform(
    async () => {
      await updateWebhookDestination(destination.id, { enabled });
      emit("change", {
        notification_destinations: {
          ...props.prefs.notification_destinations,
          [destination.id]: enabled,
        },
      });
    },
    enabled
      ? "Webhook enabled. Previous deliveries are not replayed."
      : "Webhook disabled; queued work and richer-media consent cancelled.",
  );
}
async function share(
  destination: NotificationRoutingDestination,
  enabled: boolean,
) {
  await perform(
    async () => {
      await updateWebhookDestination(destination.id, {
        share_followed_media: enabled,
      });
      confirming.value = null;
    },
    enabled
      ? "Richer media sharing confirmed for this webhook only."
      : "Public release announcements restored.",
  );
}
</script>

<template>
  <section class="webhook-destinations" aria-label="My Discord webhooks">
    <p class="hint">
      Each webhook is PUBLIC. It receives generic public release announcements
      by default. Your webhook token is saved securely and never shown to
      plugins.
    </p>
    <form class="webhook-form" @submit.prevent="add">
      <label
        >Label<input
          v-model="label"
          maxlength="80"
          placeholder="Release channel"
          :disabled="busy"
      /></label>
      <label class="endpoint"
        >Discord webhook URL<input
          v-model="url"
          type="password"
          required
          maxlength="512"
          autocomplete="off"
          placeholder="https://discord.com/api/webhooks/…"
          :disabled="busy"
      /></label>
      <button :disabled="busy || !loaded || !provider.available">
        Add webhook
      </button>
    </form>
    <p class="hint">
      Use an ordinary Discord text channel. Forum threads and DMs are not
      supported yet.
    </p>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <p v-if="status" class="success" role="status">{{ status }}</p>
    <article
      v-for="destination in listedDestinations"
      :key="destination.id"
      class="webhook-card"
    >
      <h5>{{ destination.label || "Discord webhook" }}</h5>
      <p class="hint">
        PUBLIC · External · {{ destination.active ? "Configured" : "Inactive" }}
      </p>
      <template v-if="destination.active">
        <ToggleButton
          :model-value="
            destination.enabled &&
            prefs.notification_destinations[destination.id] !== false
          "
          :label="`Use ${destination.label || 'Discord webhook'}`"
          :aria-label="`Use ${destination.label || 'Discord webhook'}`"
          :disabled="busy || !loaded || !destination.available"
          @update:model-value="use(destination, $event)"
          >Use this webhook</ToggleButton
        >
        <p class="hint">
          {{
            destination.media_disclosure_confirmed
              ? "Richer followed-media announcements confirmed for this webhook."
              : "Public release facts only; your follow choice is not shared."
          }}
        </p>
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
                'Example notification queued for this webhook. Delivery follows your saved routing.',
              )
            "
          >
            Send test notification
          </button>
          <button
            v-if="!destination.media_disclosure_confirmed"
            :disabled="busy || !destination.enabled || !destination.available"
            @click="confirming = destination.id"
          >
            Choose richer media sharing
          </button>
          <button v-else :disabled="busy" @click="share(destination, false)">
            Use public announcements only
          </button>
          <button
            :disabled="busy"
            @click="
              edits[destination.id] = destination.label || 'Discord webhook'
            "
          >
            Edit label
          </button>
          <button :disabled="busy" @click="removing = destination.id">
            Remove
          </button>
        </div>
        <div v-if="confirming === destination.id" class="confirmation">
          <p>
            Confirm sharing with
            <strong>{{ destination.label || "this webhook" }}</strong
            >: release title, season/episode/release facts, and the fact that
            you selected or followed it. Artwork is not included yet. Account
            identifiers, watch history/status, ratings, security messages and
            recovery tokens are excluded.
          </p>
          <p>
            The webhook remains PUBLIC. This confirmation applies only to this
            destination revision; disabling it clears consent.
          </p>
          <button :disabled="busy" @click="share(destination, true)">
            Confirm sharing with this webhook
          </button>
          <button :disabled="busy" @click="confirming = null">
            Keep public announcements
          </button>
        </div>
        <form
          v-if="edits[destination.id] !== undefined"
          class="webhook-form"
          @submit.prevent="
            perform(async () => {
              await updateWebhookDestination(destination.id, {
                label: edits[destination.id],
              });
              delete edits[destination.id];
            }, 'Label saved.')
          "
        >
          <label
            >Destination label<input
              v-model="edits[destination.id]"
              maxlength="80"
              :disabled="busy"
          /></label>
          <button :disabled="busy">Save label</button>
          <button
            type="button"
            :disabled="busy"
            @click="delete edits[destination.id]"
          >
            Cancel
          </button>
        </form>
      </template>
      <template v-else>
        <p class="hint">
          History and preferences are retained. Reinstallation requires a new
          webhook and fresh consent.
        </p>
        <button
          v-if="destination.reactivation_available"
          :disabled="busy || !provider.enabled"
          @click="use(destination, true)"
        >
          Reactivate this webhook
        </button>
        <button :disabled="busy" @click="removing = destination.id">
          Remove saved credentials
        </button>
      </template>
      <div v-if="removing === destination.id" class="confirmation">
        <p>
          Remove this webhook? Its token will be erased and queued deliveries
          cancelled. History and routing choices remain. To replace its URL,
          remove it and add a new webhook.
        </p>
        <button
          :disabled="busy"
          @click="
            perform(async () => {
              await removeWebhookDestination(destination.id);
              removing = null;
            }, 'Webhook removed; credentials erased.')
          "
        >
          Confirm removal
        </button>
        <button :disabled="busy" @click="removing = null">Keep webhook</button>
      </div>
    </article>
    <button
      v-if="inactiveCount"
      :aria-expanded="showInactive"
      @click="showInactive = !showInactive"
    >
      {{ showInactive ? "Hide" : "Show" }} inactive webhooks ({{
        inactiveCount
      }})
    </button>
  </section>
</template>

<style scoped>
.webhook-form,
.actions {
  display: flex;
  flex-wrap: wrap;
  align-items: end;
  gap: 10px;
  margin: 14px 0;
}
label {
  display: flex;
  flex: 1 1 170px;
  flex-direction: column;
  gap: 5px;
  font-size: 0.8rem;
}
.endpoint {
  flex-basis: 270px;
}
input {
  width: 100%;
  box-sizing: border-box;
  border: 1px solid var(--ui-border);
  border-radius: 6px;
  padding: 9px;
  color: var(--ui-text);
  background: var(--ui-surface-alt);
  font: inherit;
}
button {
  border: 1px solid var(--ui-border);
  border-radius: 6px;
  padding: 9px 12px;
  color: var(--ui-text);
  background: var(--ui-surface-alt);
  cursor: pointer;
  font: inherit;
  font-size: 0.8rem;
}
button:disabled {
  opacity: 0.55;
  cursor: default;
}
.webhook-card {
  border-top: 1px solid var(--ui-border);
  padding-top: 14px;
  margin-top: 14px;
}
h5 {
  margin: 0;
  font-size: 0.86rem;
  overflow-wrap: anywhere;
}
.hint,
.error,
.success,
.confirmation {
  font-size: 0.78rem;
  line-height: 1.6;
  color: var(--ui-dim);
}
.error {
  color: var(--ui-error);
}
.success {
  color: var(--ui-good);
}
.confirmation {
  border: 1px solid var(--ui-border);
  border-radius: 6px;
  padding: 10px;
}
</style>
