<script setup lang="ts">
import { reactive, ref } from "vue";
import { currentUser } from "../../state/auth";
import ToggleButton from "./ToggleButton.vue";
import {
  createEmailDestination,
  updateEmailDestination,
  removeEmailDestination,
  requestEmailVerification,
  confirmEmailVerification,
  revokeEmailVerification,
  sendDestinationTest,
} from "../../services/notifications";
import type {
  NotificationRoutingDestination,
  NotificationRoutingProvider,
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
const address = ref(currentUser.value?.email ?? "");
const label = ref("");
const error = ref("");
const status = ref("");
const busy = ref(false);
const challenges = reactive<Record<string, string>>({});
const codes = reactive<Record<string, string>>({});
const edits = reactive<Record<string, { address: string; label: string }>>({});
const removing = ref<string | null>(null);

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
      reason instanceof Error
        ? reason.message
        : "Could not save email destination.";
  } finally {
    busy.value = false;
  }
}
async function add() {
  await perform(async () => {
    await createEmailDestination(address.value, label.value);
    address.value = currentUser.value?.email ?? "";
    label.value = "";
  }, "Email added. Verify it to receive security notifications.");
}
async function sendCode(destination: NotificationRoutingDestination) {
  await perform(async () => {
    const result = await requestEmailVerification(destination.id);
    challenges[destination.id] = result.challenge_id;
    codes[destination.id] = "";
  }, "Verification email queued. Use its link or enter the code; both expire in 10 minutes.");
}
async function verify(destination: NotificationRoutingDestination) {
  await perform(async () => {
    await confirmEmailVerification(
      destination.id,
      challenges[destination.id]!,
      codes[destination.id] ?? "",
    );
    delete challenges[destination.id];
    delete codes[destination.id];
  }, "Email verified.");
}
async function saveEdit(destination: NotificationRoutingDestination) {
  await perform(async () => {
    const edit = edits[destination.id]!;
    await updateEmailDestination(destination.id, {
      label: edit.label,
      ...(edit.address ? { address: edit.address } : {}),
    });
    delete edits[destination.id];
    delete challenges[destination.id];
    delete codes[destination.id];
  }, "Destination saved. A replacement address needs verification again.");
}
</script>

<template>
  <section class="email-destinations" aria-label="My email destinations">
    <p class="hint">
      Add addresses for your account. New addresses are PRIVATE; verification
      proves access and makes them SECURE.
    </p>
    <p v-if="!provider.available" class="hint">
      An administrator must configure SMTP before emails can be delivered.
    </p>
    <form class="email-form" @submit.prevent="add">
      <label
        >Label<input
          v-model="label"
          maxlength="80"
          placeholder="Personal email"
          :disabled="busy"
      /></label>
      <label
        >Email address<input
          v-model="address"
          type="email"
          required
          maxlength="254"
          autocomplete="email"
          :disabled="busy"
      /></label>
      <button :disabled="busy">Add email</button>
    </form>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <p v-if="status" class="success" role="status">{{ status }}</p>
    <article
      v-for="destination in destinations.filter((d) => d.active)"
      :key="destination.id"
      class="email-card"
    >
      <h5>
        {{ destination.label || "Email" }} · {{ destination.masked_address }}
      </h5>
      <p class="hint">
        {{ destination.trust }} · External ·
        {{
          destination.provider_enabled
            ? "Account routing enabled"
            : "Account provider disabled"
        }}
      </p>
      <ToggleButton
        :model-value="
          destination.enabled &&
          prefs.notification_destinations[destination.id] !== false
        "
        :label="`Use ${destination.label || 'Email'}`"
        :aria-label="`Use ${destination.label || 'Email'}`"
        :disabled="busy || !loaded"
        @update:model-value="
          perform(async () => {
            await updateEmailDestination(destination.id, { enabled: $event });
            emit('change', {
              notification_destinations: {
                ...prefs.notification_destinations,
                [destination.id]: $event,
              },
            });
          }, 'Destination routing saved.')
        "
      >
        Use this email
      </ToggleButton>
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
              'Example notification queued for this email. Delivery follows your saved routing.',
            )
          "
        >
          Send test notification
        </button>
        <button
          :disabled="
            busy ||
            !destination.verification_available ||
            !destination.enabled ||
            !destination.provider_enabled ||
            prefs.notification_destinations[destination.id] === false
          "
          @click="sendCode(destination)"
        >
          {{
            challenges[destination.id]
              ? "Resend code"
              : "Send verification code"
          }}
        </button>
        <button
          v-if="destination.trust === 'SECURE'"
          :disabled="busy"
          @click="
            perform(
              () => revokeEmailVerification(destination.id),
              'Verification revoked; pending deliveries cancelled.',
            )
          "
        >
          Revoke verification
        </button>
        <button
          :disabled="busy"
          @click="
            edits[destination.id] = {
              address: '',
              label: destination.label || 'Email',
            }
          "
        >
          Edit
        </button>
        <button :disabled="busy" @click="removing = destination.id">
          Remove
        </button>
      </div>
      <form
        v-if="challenges[destination.id]"
        class="email-form"
        @submit.prevent="verify(destination)"
      >
        <label
          >Verification code<input
            v-model="codes[destination.id]"
            inputmode="numeric"
            autocomplete="one-time-code"
            pattern="[0-9]{8}"
            maxlength="8"
            required
            :disabled="busy"
        /></label>
        <button :disabled="busy">Verify email</button>
      </form>
      <ToggleButton
        :model-value="destination.recovery_allowed ?? false"
        :label="`Allow recovery messages to ${destination.label || 'Email'}`"
        :aria-label="`Allow recovery messages to ${destination.label || 'Email'}`"
        :disabled="
          busy || destination.trust !== 'SECURE' || !provider.secure_transport
        "
        @update:model-value="
          perform(
            () =>
              updateEmailDestination(destination.id, {
                recovery_allowed: $event,
              }),
            'Recovery-purpose choice saved.',
          )
        "
      >
        Allow recovery messages
      </ToggleButton>
      <p class="hint">
        Recovery messages require a verified external destination and an
        eligible transport. Reset and invite features are provided by plugins.
      </p>
      <form
        v-if="edits[destination.id]"
        class="email-form"
        @submit.prevent="saveEdit(destination)"
      >
        <label
          >Destination label<input
            v-model="edits[destination.id]!.label"
            maxlength="80"
        /></label>
        <label
          >Replacement email address<input
            v-model="edits[destination.id]!.address"
            type="email"
            maxlength="254"
            placeholder="Leave blank to keep this address"
        /></label>
        <p class="hint">
          Changing the address revokes verification and recovery eligibility and
          cancels queued deliveries.
        </p>
        <button :disabled="busy">Save destination</button>
        <button
          type="button"
          :disabled="busy"
          @click="delete edits[destination.id]"
        >
          Cancel edit
        </button>
      </form>
      <div v-if="removing === destination.id" class="remove-confirm">
        <p>
          Remove this email? Its address will be erased and pending deliveries
          cancelled. History and routing choices are retained.
        </p>
        <button
          :disabled="busy"
          @click="
            perform(async () => {
              await removeEmailDestination(destination.id);
              removing = null;
            }, 'Email removed.')
          "
        >
          Confirm removal
        </button>
        <button :disabled="busy" @click="removing = null">Keep email</button>
      </div>
    </article>
  </section>
</template>

<style scoped>
.email-form,
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
.email-card {
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
.remove-confirm {
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
.remove-confirm {
  border: 1px solid var(--ui-border);
  border-radius: 6px;
  padding: 10px;
}
</style>
