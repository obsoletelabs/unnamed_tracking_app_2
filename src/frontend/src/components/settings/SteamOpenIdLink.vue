<script setup lang="ts">
import { ref } from "vue";
import { beginSteamOpenId } from "../../services/steamOpenId";

defineProps<{
  verifiedSteamId: string | null;
  hasLibraryAccess: boolean;
}>();

const busy = ref(false);
const error = ref("");

async function verify() {
  busy.value = true;
  error.value = "";
  try {
    // Steam's own page handles the sign-in; this app never sees the password.
    window.location.assign(await beginSteamOpenId());
  } catch (e) {
    error.value =
      e instanceof Error ? e.message : "Couldn't start Steam sign-in.";
    busy.value = false;
  }
}
</script>

<template>
  <section class="openid" aria-labelledby="steam-openid-title">
    <h3 id="steam-openid-title">Verify your Steam account</h3>
    <p v-if="verifiedSteamId" class="verified">
      Steam ID <strong>{{ verifiedSteamId }}</strong> verified through Steam.
    </p>
    <p class="hint">
      You are sent to steamcommunity.com to sign in, then back here. Your
      password is never entered in or sent to this app. This confirms who you
      are only: it does not let the app read a private library or achievements.
      {{
        hasLibraryAccess
          ? ""
          : "Public game details also need a Steam Web API key, or use the sign-in below."
      }}
    </p>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <button
      class="ui-btn ui-btn-primary"
      type="button"
      :disabled="busy"
      @click="verify"
    >
      {{
        busy
          ? "Redirecting to Steam…"
          : verifiedSteamId
            ? "Verify again with Steam"
            : "Sign in with Steam"
      }}
    </button>
  </section>
</template>

<style scoped>
.openid {
  display: grid;
  gap: 10px;
  margin-bottom: 20px;
}
.openid h3,
.openid p {
  margin: 0;
}
.hint {
  color: var(--ui-faint);
  font-size: 0.85rem;
}
.error {
  color: var(--ui-error);
}
.openid button {
  justify-self: start;
}
</style>
