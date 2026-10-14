<script setup lang="ts">
import { onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import AppBrand from "../components/AppBrand.vue";
import PasswordInput from "../components/PasswordInput.vue";
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

onMounted(async () => {
  try {
    available.value = await invitationStatus();
  } catch {
    available.value = false;
  }
});

async function submit() {
  if (!credential.value) return;
  error.value = "";
  if (password.value !== confirmation.value) {
    error.value = "The passwords do not match.";
    return;
  }
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
    error.value =
      err instanceof Error ? err.message : "Unable to accept this invitation.";
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
        <label class="field"
          ><span>Username</span
          ><input
            v-model="username"
            autocomplete="username"
            aria-label="Username"
            aria-describedby="invite-username-help"
            maxlength="100"
            required
          /><small id="invite-username-help"
            >Use 1–100 ASCII letters, digits, underscores, hyphens or dots. No
            spaces.</small
          ></label
        >
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
