<script setup lang="ts">
import { onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import AppBrand from "../components/AppBrand.vue";
import PasswordInput from "../components/PasswordInput.vue";
import UsernameField from "../components/UsernameField.vue";
import {
  usernameFieldError,
  usernameServerError,
} from "../services/usernameValidation";
import { authLinkCredential } from "../services/passwordRecovery";
import {
  acceptInvitation,
  invitationStatus,
} from "../services/userInvitations";

const route = useRoute();
const router = useRouter();
const credential = ref(authLinkCredential(route.hash));
const username = ref("");
const password = ref("");
const confirmation = ref("");
const available = ref<boolean | null>(null);
const busy = ref(false);
const error = ref("");
const usernameServerMessage = ref<{ message: string; value: string } | null>(
  null,
);
const usernameField = ref<InstanceType<typeof UsernameField> | null>(null);
const submitAttempted = ref(false);
const message = ref("");

watch(
  () => route.hash,
  async (fragment) => {
    if (!fragment) return;
    credential.value = authLinkCredential(fragment);
    error.value = credential.value
      ? ""
      : "This invitation link is invalid. Contact the administrator.";
    message.value = "";
    username.value = password.value = confirmation.value = "";
    await router.replace({ path: route.path, hash: "" });
  },
  { immediate: true },
);

const statusFailed = ref(false);
async function checkAvailability() {
  statusFailed.value = false;
  available.value = null;
  try {
    available.value = await invitationStatus();
  } catch {
    statusFailed.value = true;
  }
}
onMounted(checkAvailability);

async function submit() {
  if (!credential.value) return;
  error.value = "";
  submitAttempted.value = true;
  usernameServerMessage.value = null;
  if (usernameFieldError(username.value)) {
    usernameField.value?.focus();
    return;
  }
  if (password.value !== confirmation.value) {
    error.value = "The passwords do not match.";
    return;
  }
  const submittedUsername = username.value;
  busy.value = true;
  try {
    message.value = await acceptInvitation(
      credential.value.id,
      credential.value.token,
      username.value.trim(),
      password.value,
    );
    credential.value = null;
    password.value = confirmation.value = "";
  } catch (err) {
    const reason = err instanceof Error ? err.message : null;
    const usernameReply = usernameServerError(reason);
    usernameServerMessage.value = usernameReply
      ? { message: usernameReply, value: submittedUsername }
      : null;
    if (usernameServerMessage.value) usernameField.value?.focus();
    else error.value = reason ?? "Unable to accept this invitation.";
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <main class="invite-page">
    <form class="invite-card" @submit.prevent="submit">
      <AppBrand compact />
      <h1>Create your account</h1>
      <p v-if="message" role="status">{{ message }}</p>
      <template v-else-if="statusFailed">
        <p role="alert">
          Could not check whether invitations are available. Your link is still
          valid.
        </p>
        <button
          class="ui-btn ui-btn-secondary"
          type="button"
          @click="checkAvailability"
        >
          Try again
        </button>
      </template>
      <p v-else-if="available === null">Checking invitation availability…</p>
      <p v-else-if="!available">
        Invitations are currently unavailable. Contact the administrator.
      </p>
      <p v-else-if="!credential">
        Open the invitation link from your email to create an account.
      </p>
      <template v-else>
        <p>
          Choose a username and password. Your account will use the email
          address that received this invitation.
        </p>
        <UsernameField
          ref="usernameField"
          v-model="username"
          autocomplete="username"
          required
          :server-error="usernameServerMessage"
          :show-errors="submitAttempted"
        />
        <label class="field"
          ><span>Password</span
          ><PasswordInput
            v-model="password"
            mode="new"
            autocomplete="new-password"
            required
        /></label>
        <label class="field"
          ><span>Confirm password</span
          ><PasswordInput
            v-model="confirmation"
            autocomplete="new-password"
            required
        /></label>
        <button class="primary" type="submit" :disabled="busy">
          {{ busy ? "Creating account…" : "Create account" }}
        </button>
      </template>
      <p v-if="error" role="alert">{{ error }}</p>
      <RouterLink to="/login">{{
        message ? "Sign in" : "Back to sign in"
      }}</RouterLink>
    </form>
  </main>
</template>

<style scoped>
.invite-page {
  min-height: 80vh;
  display: grid;
  place-items: center;
  padding: 1rem;
  box-sizing: border-box;
}
.invite-card {
  width: min(100%, 30rem);
  min-width: 0;
  box-sizing: border-box;
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 1rem;
  padding: 1.5rem;
  border: 1px solid var(--ui-border);
  border-radius: 16px;
  background: var(--ui-surface);
  color: var(--ui-text);
}
h1,
p {
  margin: 0;
}
.field {
  display: grid;
  gap: 0.4rem;
  min-width: 0;
}
input {
  width: 100%;
  min-width: 0;
  box-sizing: border-box;
  padding: 12px;
  border-radius: 8px;
  border: 1px solid var(--ui-border);
  background: var(--ui-bg);
  color: var(--ui-text);
}
button {
  padding: 12px;
  border: 1px solid var(--ui-border-strong);
  border-radius: 8px;
  background: var(--ui-accent);
  color: var(--ui-on-accent);
  cursor: pointer;
}
button:disabled {
  opacity: 0.6;
  cursor: wait;
}
a {
  color: var(--ui-accent);
  text-align: center;
}
p {
  line-height: 1.6;
  color: var(--ui-dim);
}
[role="alert"] {
  color: var(--ui-danger);
}
</style>
