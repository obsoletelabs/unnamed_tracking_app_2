<script setup lang="ts">
import {
  computed,
  nextTick,
  onBeforeUnmount,
  onMounted,
  ref,
  watch,
} from "vue";
import UiModal from "../UiModal.vue";
import PasswordInput from "../PasswordInput.vue";
import SteamOpenIdLink from "./SteamOpenIdLink.vue";
import { useConfirm } from "../../state/dialog";
import {
  beginSteamAccount,
  beginSteamQr,
  cancelSteamLogin,
  disconnectSteam,
  fetchSteamConnection,
  pollSteamLogin,
  STEAM_GUARD_DEVICE_CODE,
  STEAM_GUARD_EMAIL_CODE,
  SteamConnectionError,
  submitSteamGuard,
} from "../../services/steamConnection";
import type {
  SteamConnection,
  SteamGuardCodeType,
} from "../../services/steamConnection";

const emit = defineEmits<{ close: []; changed: []; legacy: [] }>();
const connection = ref<SteamConnection | null>(null);
const method = ref<"qr" | "account">("qr");
const accountName = ref("");
const password = ref("");
const guardCode = ref("");
const guardType = ref<SteamGuardCodeType>(STEAM_GUARD_DEVICE_CODE);
// `busy` is for user actions only; background polls must not disable the
// Guard code field while someone is typing into it.
const busy = ref(false);
const loading = ref(true);
const error = ref("");
// Steam was unreachable or rate limiting; the same attempt is retried.
const retrying = ref(false);
// An account session can be replaced, e.g. after Steam revokes it.
const signingInAgain = ref(false);
const qrImage = ref("");
const guardInput = ref<HTMLInputElement | null>(null);
const confirm = useConfirm();
let timer: ReturnType<typeof setTimeout> | undefined;
let inFlight: Promise<void> | null = null;
let closed = false;
let revision = 0;

const pending = computed(() => connection.value?.pending);
const guardTypes = computed(
  () =>
    pending.value?.confirmation_types.filter(
      (type): type is SteamGuardCodeType =>
        type === STEAM_GUARD_EMAIL_CODE || type === STEAM_GUARD_DEVICE_CODE,
    ) ?? [],
);
const guardDescription = computed(() =>
  guardType.value === STEAM_GUARD_EMAIL_CODE
    ? "Enter the code Steam emailed you."
    : "Enter the code from Steam Guard in your mobile app.",
);

watch(
  () => pending.value?.challenge_url,
  async (url) => {
    qrImage.value = "";
    if (!url) return;
    try {
      const { default: QRCode } = await import("qrcode");
      const image = await QRCode.toDataURL(url, {
        width: 256,
        margin: 4,
        errorCorrectionLevel: "M",
      });
      if (!closed && pending.value?.challenge_url === url)
        qrImage.value = image;
    } catch {
      error.value = "Couldn't display the QR code. Try account sign-in.";
    }
  },
);
// Every poll yields a new array, so watch its contents and keep the person's
// choice while Steam still offers it.
watch(
  () => guardTypes.value.join(","),
  async () => {
    const types = guardTypes.value;
    if (!types.length) return;
    if (!types.includes(guardType.value))
      guardType.value = types.includes(STEAM_GUARD_DEVICE_CODE)
        ? STEAM_GUARD_DEVICE_CODE
        : STEAM_GUARD_EMAIL_CODE;
    await nextTick();
    guardInput.value?.focus();
  },
);
const statusText = computed(() =>
  retrying.value
    ? "Steam isn't responding. Retrying…"
    : pending.value?.phase === "waiting"
      ? "Waiting for Steam approval…"
      : "Checking sign-in…",
);
const approvalHint = computed(() => {
  const types = pending.value?.confirmation_types ?? [];
  const app = types.includes(4);
  const email = types.includes(5);
  if (app && email)
    return "Approve the sign-in in the Steam mobile app or from Steam's email.";
  if (app) return "Approve the sign-in in the Steam mobile app.";
  if (email) return "Approve the sign-in from the email Steam sent you.";
  return "";
});

function stopPolling() {
  clearTimeout(timer);
  timer = undefined;
}
function schedule() {
  stopPolling();
  if (!closed && pending.value)
    timer = setTimeout(
      check,
      Math.max(3000, pending.value.interval * 1000 + 250),
    );
}
async function accept(value: SteamConnection, requestRevision: number) {
  if (closed || requestRevision !== revision) {
    if (value.pending)
      await cancelSteamLogin(value.pending.attempt_id).catch(() => undefined);
    return;
  }
  const wasPending = !!connection.value?.pending;
  connection.value = value;
  if (!value.pending) retrying.value = false;
  if (wasPending && !value.pending && value.connected) {
    error.value = "";
    signingInAgain.value = false;
    emit("changed");
  }
  schedule();
}
// After a failure the server may already have ended the attempt; show its
// real state instead of a QR or code form that can no longer complete.
async function resync(requestRevision: number) {
  try {
    await accept(await fetchSteamConnection(), requestRevision);
  } catch {
    stopPolling();
  }
}
async function poll() {
  if (!pending.value || closed) return;
  const requestRevision = revision;
  if (pending.value.expires_at * 1000 <= Date.now()) {
    stopPolling();
    error.value = "Steam sign-in expired. Start a new sign-in.";
    const expired = await cancelSteamLogin(pending.value.attempt_id).catch(
      () => null,
    );
    if (expired) await accept(expired, requestRevision);
    return;
  }
  try {
    const value =
      pending.value.phase === "waiting"
        ? await pollSteamLogin(pending.value.attempt_id)
        : await fetchSteamConnection();
    retrying.value = false;
    await accept(value, requestRevision);
  } catch (e) {
    if (closed || requestRevision !== revision) return;
    if (e instanceof SteamConnectionError && e.status === 429) schedule();
    else if (e instanceof SteamConnectionError && e.status === 503) {
      retrying.value = true;
      schedule();
    } else {
      retrying.value = false;
      error.value = e instanceof Error ? e.message : "Steam sign-in failed.";
      stopPolling();
      await resync(requestRevision);
    }
  }
}
function check() {
  // a user action owns the attempt; it reschedules polling when done
  if (inFlight || busy.value) return;
  inFlight = poll().finally(() => {
    inFlight = null;
  });
}

async function signIn() {
  const requestRevision = ++revision;
  busy.value = true;
  error.value = "";
  const transientPassword = password.value;
  password.value = "";
  try {
    await accept(
      method.value === "qr"
        ? await beginSteamQr()
        : await beginSteamAccount(accountName.value, transientPassword),
      requestRevision,
    );
  } catch (e) {
    if (closed || requestRevision !== revision) return;
    error.value =
      e instanceof Error ? e.message : "Couldn't start Steam sign-in.";
  } finally {
    if (requestRevision === revision) busy.value = false;
  }
}
async function sendGuard() {
  if (!pending.value || busy.value) return;
  const requestRevision = revision;
  busy.value = true;
  error.value = "";
  stopPolling();
  // the server rejects a code while a poll is still with Steam
  if (inFlight) await inFlight;
  if (!pending.value || closed || requestRevision !== revision) {
    busy.value = false;
    return;
  }
  stopPolling();
  // pasted or autofilled codes often carry spaces; Steam's codes are uppercase
  const transientCode = guardCode.value.replace(/\s+/g, "").toUpperCase();
  try {
    await accept(
      await submitSteamGuard(
        pending.value.attempt_id,
        transientCode,
        guardType.value,
      ),
      requestRevision,
    );
    guardCode.value = "";
  } catch (e) {
    if (closed || requestRevision !== revision) return;
    error.value =
      e instanceof Error ? e.message : "Steam did not accept the code.";
    await resync(requestRevision);
  } finally {
    if (requestRevision === revision) busy.value = false;
  }
}
async function cancel() {
  if (busy.value) return;
  const requestRevision = ++revision;
  stopPolling();
  busy.value = true;
  try {
    if (pending.value)
      await accept(
        await cancelSteamLogin(pending.value.attempt_id),
        requestRevision,
      );
    error.value = "";
    retrying.value = false;
  } catch (e) {
    error.value =
      e instanceof Error ? e.message : "Couldn't cancel Steam sign-in.";
    // keep tracking the attempt rather than freezing on a stale QR
    await resync(requestRevision);
  } finally {
    busy.value = false;
  }
}
async function disconnect() {
  if (
    !(await confirm({
      title: "Disconnect Steam?",
      message:
        "Your imported games, achievements and confirmed copy groups will be kept.",
      confirmLabel: "Disconnect",
      danger: true,
    }))
  )
    return;
  ++revision;
  busy.value = true;
  error.value = "";
  stopPolling();
  try {
    connection.value = await disconnectSteam();
    signingInAgain.value = false;
    emit("changed");
  } catch (e) {
    error.value = e instanceof Error ? e.message : "Couldn't disconnect Steam.";
  } finally {
    busy.value = false;
  }
}
// Unmounting cancels any pending attempt, so closing never waits on the network.
function close() {
  closed = true;
  stopPolling();
  emit("close");
}
// Until the connection is known the dialog must not offer a sign-in: an
// already connected account would look unconnected and could be replaced.
const loadFailed = ref(false);
async function loadConnection() {
  const requestRevision = revision;
  loading.value = true;
  loadFailed.value = false;
  error.value = "";
  try {
    await accept(await fetchSteamConnection(), requestRevision);
  } catch (e) {
    loadFailed.value = true;
    error.value =
      e instanceof Error ? e.message : "Couldn't load Steam connection.";
  } finally {
    loading.value = false;
  }
}
onMounted(loadConnection);
onBeforeUnmount(() => {
  closed = true;
  ++revision;
  stopPolling();
  password.value = guardCode.value = "";
  if (pending.value)
    void cancelSteamLogin(pending.value.attempt_id).catch(() => undefined);
});
</script>

<template>
  <UiModal
    :title="connection?.connected ? 'Steam sign-in' : 'Connect Steam'"
    description="Import your library and achievements by signing in to Steam."
    @close="close"
  >
    <p v-if="loading" class="hint" role="status">Loading connection…</p>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <button
      v-if="loadFailed"
      class="ui-btn ui-btn-secondary"
      type="button"
      @click="loadConnection"
    >
      Try again
    </button>
    <template v-if="!loading && !loadFailed">
      <SteamOpenIdLink
        v-if="!pending"
        :verified-steam-id="connection?.verified_steam_id ?? null"
        :has-library-access="!!connection?.connected"
      />
      <div v-if="connection?.connected && !pending" class="connected">
        <strong
          >{{ connection.display_name || "Steam account" }} connected</strong
        >
        <p class="hint">
          {{
            connection.method === "api_key"
              ? "Using legacy API credentials. Sign in below to enable account-owner imports."
              : "Your session is renewed when you import. Private achievement progress uses your signed-in account."
          }}
        </p>
        <div class="connected-actions">
          <button
            v-if="connection.method === 'account' && !signingInAgain"
            class="ui-btn ui-btn-secondary"
            type="button"
            :disabled="busy"
            @click="signingInAgain = true"
          >
            Sign in again
          </button>
          <button
            class="ui-btn ui-btn-ghost"
            type="button"
            :disabled="busy"
            @click="disconnect"
          >
            Disconnect
          </button>
        </div>
      </div>
      <div v-if="pending" class="pending">
        <template v-if="pending.challenge_url">
          <img
            v-if="qrImage"
            :src="qrImage"
            class="qr"
            width="256"
            height="256"
            alt="Steam sign-in QR code"
          />
          <div v-else class="qr qr-placeholder" aria-hidden="true"></div>
          <p>
            Scan with Steam Guard in the Steam mobile app, then approve the
            sign-in to Unnamed Tracking App.
          </p>
        </template>
        <p v-else-if="approvalHint" class="hint">{{ approvalHint }}</p>
        <form
          v-if="guardTypes.length"
          class="login-form"
          @submit.prevent="sendGuard"
        >
          <label v-if="guardTypes.length > 1"
            >Confirmation method<select v-model="guardType" class="ui-field">
              <option :value="STEAM_GUARD_DEVICE_CODE">
                Steam Guard app code
              </option>
              <option :value="STEAM_GUARD_EMAIL_CODE">Email code</option>
            </select></label
          >
          <label
            >{{ guardDescription
            }}<input
              ref="guardInput"
              v-model="guardCode"
              class="ui-field"
              type="text"
              autocomplete="one-time-code"
              autocapitalize="characters"
              autocorrect="off"
              spellcheck="false"
              maxlength="16"
              required
              :disabled="busy"
          /></label>
          <button class="ui-btn ui-btn-primary" type="submit" :disabled="busy">
            {{ busy ? "Checking code…" : "Submit Steam Guard code" }}
          </button>
        </form>
        <p class="hint" role="status">{{ statusText }}</p>
        <button
          class="ui-btn ui-btn-ghost"
          type="button"
          :disabled="busy"
          @click="cancel"
        >
          Cancel sign-in
        </button>
      </div>
      <form
        v-else-if="
          !connection?.connected ||
          connection.method === 'api_key' ||
          signingInAgain
        "
        class="login-form"
        @submit.prevent="signIn"
      >
        <div class="methods" role="group" aria-label="Steam sign-in method">
          <button
            class="ui-btn"
            :class="method === 'qr' ? 'ui-btn-primary' : 'ui-btn-ghost'"
            type="button"
            :aria-pressed="method === 'qr'"
            :disabled="busy"
            @click="method = 'qr'"
          >
            Steam Guard QR
          </button>
          <button
            class="ui-btn"
            :class="method === 'account' ? 'ui-btn-primary' : 'ui-btn-ghost'"
            type="button"
            :aria-pressed="method === 'account'"
            :disabled="busy"
            @click="method = 'account'"
          >
            Account sign-in
          </button>
        </div>
        <p v-if="method === 'qr'" class="hint">
          Use the Steam mobile app to scan and approve. No API key or profile ID
          is needed.
        </p>
        <template v-else>
          <label
            >Steam account name<input
              v-model="accountName"
              class="ui-field"
              autocomplete="username"
              maxlength="100"
              required
              :disabled="busy"
          /></label>
          <label
            >Password<PasswordInput
              v-model="password"
              autocomplete="current-password"
              :required="true"
              :disabled="busy"
          /></label>
          <p class="hint">
            Your password is encrypted for Steam and used once. The app stores
            an encrypted Steam session for future imports.
          </p>
        </template>
        <button class="ui-btn ui-btn-primary" type="submit" :disabled="busy">
          {{
            busy
              ? "Connecting…"
              : method === "qr"
                ? "Show Steam QR code"
                : "Sign in to Steam"
          }}
        </button>
        <button
          v-if="!connection?.connected"
          class="link-btn"
          type="button"
          @click="emit('legacy')"
        >
          Use a Web API key for a public profile instead
        </button>
      </form>
    </template>
    <template #footer
      ><button class="ui-btn ui-btn-ghost" type="button" @click="close">
        Close
      </button></template
    >
  </UiModal>
</template>

<style scoped>
.login-form,
.pending {
  display: grid;
  gap: 16px;
}
.login-form label {
  display: grid;
  gap: 8px;
}
.methods {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.hint {
  color: var(--ui-faint);
  font-size: 0.85rem;
}
.error {
  color: var(--ui-error);
}
.qr {
  justify-self: center;
  max-width: 100%;
  height: auto;
  background: white;
}
.qr-placeholder {
  width: 256px;
  aspect-ratio: 1;
  border-radius: var(--ui-radius-control);
  background: color-mix(in srgb, var(--ui-text) 6%, transparent);
}
.connected {
  margin-bottom: 20px;
}
.connected-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.link-btn {
  justify-self: start;
  padding: 0;
  border: none;
  background: none;
  color: var(--ui-accent);
  font: inherit;
  font-size: 0.85rem;
  cursor: pointer;
  text-decoration: underline;
}
.pending p {
  margin: 0;
}
</style>
