<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import PasswordInput from "../PasswordInput.vue";
import UiModal from "../UiModal.vue";
import { useConfirm } from "../../state/dialog";
import { isConnectionConnected } from "../../composables/useConnections";
import type {
  ConnectionConfig,
  ConnectionsState,
} from "../../composables/useConnections";

const props = defineProps<{
  connection: ConnectionConfig;
  state: ConnectionsState;
}>();
const emit = defineEmits<{ close: []; signIn: [] }>();
const confirm = useConfirm();
// local bindings: v-model must not write through the prop object
const { fieldValues, npssoToken } = props.state;

const key = computed(() => props.connection.key);
const status = computed(() => props.state.credentialStatus[key.value]);
const connected = computed(() =>
  props.connection.kind === "psn"
    ? props.state.psnStatus.value.connected
    : isConnectionConnected(status.value),
);
const profile = computed(() => {
  if (props.connection.kind === "psn" && connected.value) {
    const psn = props.state.psnStatus.value;
    return { name: psn.display_name, avatar: psn.avatar_url };
  }
  return {
    name: status.value?.display_name,
    avatar: status.value?.avatar_url,
  };
});
const lastSynced = computed(() => props.state.formatLastSynced(status.value));
const syncing = computed(() =>
  props.connection.syncProvider
    ? props.state.librarySyncing[props.connection.syncProvider]
    : false,
);

const disconnecting = ref(false);

onMounted(() => {
  props.state.clearMessages(key.value);
  if (props.connection.kind === "psn") void props.state.loadPsn();
});

async function disconnect() {
  if (disconnecting.value) return;
  const confirmed = await confirm({
    title: `Disconnect ${props.connection.label}?`,
    message:
      props.connection.kind === "psn"
        ? "You'll need to paste your npsso token again to reconnect. Games already imported stay in your library."
        : "Your saved sign-in details are removed. Games already imported stay in your library.",
    confirmLabel: "Disconnect",
    danger: true,
  });
  if (!confirmed) return;
  disconnecting.value = true;
  try {
    if (props.connection.kind === "psn")
      await props.state.handleDisconnectPsn();
    else await props.state.clearConnection(props.connection);
  } finally {
    disconnecting.value = false;
  }
}
</script>

<template>
  <UiModal
    :title="connection.label"
    :description="connection.summary"
    @close="emit('close')"
  >
    <div class="connection-dialog">
      <p class="status-line">
        <span
          v-if="!(connection.kind === 'psn' && state.psnLoading.value)"
          class="pill"
          :class="{ ok: connected }"
          >{{ connected ? "Connected" : "Not connected" }}</span
        >
        <span v-if="profile.name" class="profile">
          <img v-if="profile.avatar" :src="profile.avatar" alt="" />
          {{ profile.name }}
        </span>
        <span v-if="connected && lastSynced" class="synced">{{
          lastSynced
        }}</span>
      </p>
      <p class="hint">{{ connection.description }}</p>
      <a
        v-if="connection.linkUrl"
        class="provider-link"
        :href="connection.linkUrl"
        target="_blank"
        rel="noopener noreferrer"
        >{{ connection.linkLabel ?? connection.linkUrl }}</a
      >

      <template v-if="connection.kind === 'psn'">
        <p v-if="state.psnLoading.value" class="hint" role="status">Loading…</p>
        <!-- shown while connected too: a fresh token is how PSN reconnects -->
        <form
          v-else
          class="connection-form"
          @submit.prevent="state.handleConnectPsn()"
        >
          <p v-if="connected" class="hint">
            Paste a fresh npsso token to reconnect, for example after
            PlayStation signs you out.
          </p>
          <label class="field">
            <span>npsso token</span>
            <PasswordInput
              v-model="npssoToken"
              placeholder="Paste your npsso token"
            />
          </label>
          <p v-if="state.psnError.value" class="form-error" role="alert">
            {{ state.psnError.value }}
          </p>
          <div class="actions">
            <button
              type="submit"
              class="ui-btn ui-btn-primary"
              :disabled="state.psnConnecting.value"
            >
              {{
                state.psnConnecting.value
                  ? "Connecting…"
                  : connected
                    ? "Reconnect"
                    : "Connect"
              }}
            </button>
          </div>
        </form>
      </template>

      <template v-else>
        <div v-if="connection.signIn" class="actions">
          <button
            type="button"
            class="ui-btn ui-btn-primary"
            @click="emit('signIn')"
          >
            {{
              status?.connection_method === "account"
                ? "Manage Steam sign-in"
                : "Sign in with Steam"
            }}
          </button>
        </div>
        <p
          v-if="status?.connection_method === 'account' && state.cardError[key]"
          class="form-error"
          role="alert"
        >
          {{ state.cardError[key] }}
        </p>
        <component
          :is="connection.signIn ? 'details' : 'div'"
          v-if="status?.connection_method !== 'account'"
          class="credential-fields"
          :open="connection.signIn && !connected ? true : undefined"
        >
          <summary v-if="connection.signIn">
            Legacy API credentials (public profiles)
          </summary>
          <form
            class="connection-form"
            @submit.prevent="state.saveConnection(connection)"
          >
            <label
              v-for="field in connection.fields"
              :key="field.key"
              class="field"
            >
              <span>{{ field.label }}</span>
              <PasswordInput
                v-if="field.type === 'password'"
                v-model="fieldValues[key][field.key]"
                :placeholder="
                  state.passwordPlaceholder(key, field.key, field.label)
                "
              />
              <input
                v-else
                v-model="fieldValues[key][field.key]"
                class="ui-field"
                type="text"
                autocomplete="off"
                :placeholder="`Paste your ${field.label.toLowerCase()}`"
              />
            </label>
            <p v-if="state.cardError[key]" class="form-error" role="alert">
              {{ state.cardError[key] }}
            </p>
            <p v-if="state.cardInfo[key]" class="form-success" role="status">
              {{ state.cardInfo[key] }}
            </p>
            <div class="actions">
              <button
                type="submit"
                class="ui-btn ui-btn-primary"
                :disabled="state.cardSaving[key]"
              >
                {{
                  state.cardSaving[key]
                    ? "Saving…"
                    : connected
                      ? "Save changes"
                      : "Connect"
                }}
              </button>
            </div>
          </form>
        </component>
      </template>
    </div>

    <template #footer>
      <button
        v-if="connection.syncProvider && connected"
        type="button"
        class="ui-btn ui-btn-secondary"
        :disabled="syncing"
        @click="state.handleSyncLibrary(connection.syncProvider)"
      >
        {{ syncing ? "Syncing…" : "Sync now" }}
      </button>
      <button
        v-if="connected"
        type="button"
        class="ui-btn ui-btn-danger-soft"
        :disabled="disconnecting"
        @click="disconnect"
      >
        {{ disconnecting ? "Disconnecting…" : "Disconnect" }}
      </button>
      <button type="button" class="ui-btn ui-btn-ghost" @click="emit('close')">
        Close
      </button>
    </template>
  </UiModal>
</template>

<style scoped>
.connection-dialog {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.status-line {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
  margin: 0;
}
.pill {
  font-size: 0.72rem;
  font-weight: 700;
  padding: 3px 9px;
  border-radius: 999px;
  color: var(--ui-dim);
  background: color-mix(in srgb, var(--ui-text) 6%, transparent);
}
.pill.ok {
  color: var(--ui-good);
  background: rgba(34, 197, 94, 0.12);
}
.profile {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 0.85rem;
  color: var(--ui-text);
}
.profile img {
  width: 20px;
  height: 20px;
  border-radius: 50%;
}
.synced {
  font-size: 0.78rem;
  color: var(--ui-faint);
}
.hint {
  margin: 0;
  color: var(--ui-dim);
  font-size: 0.85rem;
  line-height: 1.6;
}
.provider-link {
  align-self: flex-start;
  font-size: 0.85rem;
  color: var(--ui-accent);
}
.connection-form {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.field {
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 0.82rem;
  color: var(--ui-dim);
}
.actions {
  display: flex;
  gap: 8px;
}
.form-error {
  margin: 0;
  color: var(--ui-error);
  font-size: 0.85rem;
}
.credential-fields summary {
  cursor: pointer;
  color: var(--ui-dim);
  font-size: 0.85rem;
  margin-bottom: 12px;
}
.form-success {
  margin: 0;
  color: var(--ui-good);
  font-size: 0.85rem;
}
</style>
