<script setup lang="ts">
import { onMounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import AppBrand from "../components/AppBrand.vue";
import PasswordInput from "../components/PasswordInput.vue";
import {
  completePasswordReset,
  passwordResetStatus,
  requestPasswordReset,
  resetCredential,
} from "../services/passwordRecovery";

const route = useRoute();
const router = useRouter();
const credential = resetCredential(route.hash);
const identifier = ref("");
const password = ref("");
const confirmation = ref("");
const available = ref<boolean | null>(null);
const busy = ref(false);
const message = ref("");
const error = ref("");
const finished = ref(false);

onMounted(async () => {
  // Keep bearer material in this page's memory, out of navigation/return-path state.
  if (route.hash) await router.replace({ path: route.path, hash: "" });
  try {
    available.value = await passwordResetStatus();
  } catch {
    available.value = false;
  }
});

async function submit() {
  error.value = "";
  if (credential && password.value !== confirmation.value) {
    error.value = "The passwords do not match.";
    return;
  }
  busy.value = true;
  try {
    message.value = credential
      ? await completePasswordReset(
          credential.id,
          credential.token,
          password.value,
        )
      : await requestPasswordReset(identifier.value.trim());
    finished.value = true;
    password.value = "";
    confirmation.value = "";
  } catch (err) {
    error.value =
      err instanceof Error ? err.message : "Unable to complete this request.";
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <main class="reset-page">
    <form class="reset-card" @submit.prevent="submit">
      <AppBrand compact />
      <h1>
        {{ credential ? "Choose a new password" : "Reset your password" }}
      </h1>
      <p v-if="available === null">Checking recovery availability…</p>
      <p v-else-if="!available">
        Password recovery is currently unavailable. Contact your administrator.
      </p>
      <template v-else-if="!finished">
        <p>
          {{
            credential
              ? "Changing your password signs out existing sessions. Your API keys stay active."
              : "Enter your username or app email. A link will be sent if your account has an eligible recovery destination."
          }}
        </p>
        <template v-if="credential">
          <label class="field"
            ><span>New password</span
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
              mode="new"
              autocomplete="new-password"
              required
          /></label>
        </template>
        <label v-else class="field"
          ><span>Username or email</span
          ><input
            v-model="identifier"
            autocomplete="username"
            maxlength="320"
            required
        /></label>
        <p v-if="error" class="error" role="alert">{{ error }}</p>
        <button type="submit" :disabled="busy">
          {{
            busy
              ? "Please wait…"
              : credential
                ? "Change password"
                : "Send reset link"
          }}
        </button>
      </template>
      <p v-if="message" role="status">{{ message }}</p>
      <RouterLink to="/login">Back to sign in</RouterLink>
    </form>
  </main>
</template>

<style scoped>
.reset-page {
  min-height: 100vh;
  display: grid;
  place-items: center;
  padding: 24px 16px;
  background: var(--ui-bg);
}
.reset-card {
  width: min(100%, 420px);
  display: flex;
  flex-direction: column;
  gap: 18px;
  padding: 30px;
  border: 1px solid var(--ui-border);
  border-radius: 16px;
  background: var(--ui-surface);
  color: var(--ui-text);
}
h1 {
  margin: 0;
  font-size: 24px;
}
p {
  margin: 0;
  color: var(--ui-dim);
  line-height: 1.6;
}
.field {
  display: flex;
  flex-direction: column;
  gap: 8px;
  font-size: 13px;
}
input {
  min-width: 0;
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
.error {
  color: var(--ui-danger);
}
@media (max-width: 480px) {
  .reset-card {
    padding: 24px;
  }
}
</style>
