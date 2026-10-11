<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import UiModal from "../UiModal.vue";
import PasswordInput from "../PasswordInput.vue";
import { useConfirm } from "../../state/dialog";
import {
  beginSteamAccount,
  beginSteamQr,
  cancelSteamLogin,
  disconnectSteam,
  fetchSteamConnection,
  pollSteamLogin,
  SteamConnectionError,
  submitSteamGuard,
} from "../../services/steamConnection";
import type { SteamConnection } from "../../services/steamConnection";

const emit = defineEmits<{ close: []; changed: [] }>();
const connection = ref<SteamConnection | null>(null);
const method = ref<"qr" | "account">("qr");
const accountName = ref("");
const password = ref("");
const guardCode = ref("");
const guardType = ref<1 | 2>(2);
const busy = ref(false);
const loading = ref(true);
const error = ref("");
const qrImage = ref("");
const confirm = useConfirm();
let timer: ReturnType<typeof setTimeout> | undefined;
let closed = false;
let revision = 0;

const pending = computed(() => connection.value?.pending);
const guardTypes = computed(
  () =>
    pending.value?.confirmation_types.filter(
      (type) => type === 1 || type === 2,
    ) ?? [],
);
const guardDescription = computed(() =>
  guardType.value === 1
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
watch(guardTypes, (types) => {
  guardType.value = types.includes(2) ? 2 : 1;
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
  if (wasPending && !value.pending && value.connected) emit("changed");
  schedule();
}
async function check() {
  if (!pending.value || closed || busy.value) return;
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
  busy.value = true;
  try {
    await accept(
      pending.value.phase === "waiting"
        ? await pollSteamLogin(pending.value.attempt_id)
        : await fetchSteamConnection(),
      requestRevision,
    );
  } catch (e) {
    if (closed || requestRevision !== revision) return;
    if (e instanceof SteamConnectionError && e.status === 429) schedule();
    else {
      error.value = e instanceof Error ? e.message : "Steam sign-in failed.";
      stopPolling();
    }
  } finally {
    if (requestRevision === revision) busy.value = false;
  }
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
  if (!pending.value) return;
  const requestRevision = revision;
  busy.value = true;
  error.value = "";
  stopPolling();
  const transientCode = guardCode.value;
  guardCode.value = "";
  try {
    await accept(
      await submitSteamGuard(
        pending.value.attempt_id,
        transientCode,
        guardType.value,
      ),
      requestRevision,
    );
  } catch (e) {
    if (closed || requestRevision !== revision) return;
    error.value =
      e instanceof Error ? e.message : "Steam did not accept the code.";
    try {
      await accept(await fetchSteamConnection(), requestRevision);
    } catch {
      stopPolling();
    }
  } finally {
    if (requestRevision === revision) busy.value = false;
  }
}
async function cancel() {
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
  } catch (e) {
    error.value =
      e instanceof Error ? e.message : "Couldn't cancel Steam sign-in.";
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
  stopPolling();
  try {
    connection.value = await disconnectSteam();
    emit("changed");
  } catch (e) {
    error.value = e instanceof Error ? e.message : "Couldn't disconnect Steam.";
  } finally {
    busy.value = false;
  }
}
async function close() {
  closed = true;
  ++revision;
  stopPolling();
  password.value = guardCode.value = "";
  if (pending.value) {
    connection.value = await cancelSteamLogin(pending.value.attempt_id).catch(
      () => connection.value,
    );
  }
  emit("close");
}
onMounted(async () => {
  const requestRevision = revision;
  try {
    await accept(await fetchSteamConnection(), requestRevision);
  } catch (e) {
    error.value =
      e instanceof Error ? e.message : "Couldn't load Steam connection.";
  } finally {
    loading.value = false;
  }
});
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
    title="Connect Steam"
    description="Import your library and achievements by signing in to Steam."
    @close="close"
  >
    <p v-if="loading" class="hint">Loading connection…</p>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <template v-if="!loading">
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
        <button
          class="ui-btn ui-btn-ghost"
          type="button"
          :disabled="busy"
          @click="disconnect"
        >
          Disconnect
        </button>
      </div>
      <div v-if="pending" class="pending" aria-live="polite">
        <template v-if="pending.challenge_url">
          <img
            v-if="qrImage"
            :src="qrImage"
            class="qr"
            width="256"
            height="256"
            alt="Steam sign-in QR code"
          />
          <p>
            Scan with Steam Guard in the Steam mobile app, then approve the
            sign-in to Unnamed Tracking App.
          </p>
        </template>
        <p v-else class="hint">
          Approve the sign-in in Steam Guard or through Steam's email
          confirmation.
        </p>
        <form
          v-if="guardTypes.length"
          class="login-form"
          @submit.prevent="sendGuard"
        >
          <label v-if="guardTypes.length > 1"
            >Confirmation method<select v-model="guardType" class="ui-field">
              <option :value="2">Steam Guard app code</option>
              <option :value="1">Email code</option>
            </select></label
          >
          <label
            >{{ guardDescription
            }}<input
              v-model="guardCode"
              class="ui-field"
              type="text"
              autocomplete="one-time-code"
              maxlength="10"
              required
              :disabled="busy"
          /></label>
          <button class="ui-btn ui-btn-primary" type="submit" :disabled="busy">
            Submit Steam Guard code
          </button>
        </form>
        <p class="hint">
          {{
            busy || pending.phase !== "waiting"
              ? "Checking sign-in…"
              : "Waiting for Steam approval…"
          }}
        </p>
        <button class="ui-btn ui-btn-ghost" type="button" @click="cancel">
          Cancel sign-in
        </button>
      </div>
      <form
        v-else-if="!connection?.connected || connection.method === 'api_key'"
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
.connected {
  margin-bottom: 20px;
}
.pending p {
  margin: 0;
}
</style>
