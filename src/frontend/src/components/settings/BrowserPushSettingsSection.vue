<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import PasswordInput from "../PasswordInput.vue";
import {
  fetchDeploymentSettings,
  updateDeploymentSettings,
  type DeploymentSettings,
} from "../../services/deploymentSettings";
import { generateBrowserPushKey } from "../../services/notifications";
const settings = ref<DeploymentSettings | null>(null);
const subject = ref("");
const privateKey = ref("");
const confirmed = ref(false);
const busy = ref(false);
const error = ref("");
const status = ref("");
const configured = computed(() =>
  Boolean(settings.value?.providers.web_push_vapid_private_key_configured),
);
const locked = computed(() =>
  Boolean(settings.value?.provider_locks.web_push_vapid_private_key),
);
function apply(value: DeploymentSettings) {
  settings.value = value;
  subject.value = value.browser_push?.subject ?? "";
  privateKey.value = "";
  confirmed.value = false;
}
async function perform(operation: () => Promise<unknown>, message: string) {
  busy.value = true;
  error.value = "";
  status.value = "";
  try {
    await operation();
    apply(await fetchDeploymentSettings());
    status.value = message;
  } catch (reason) {
    error.value =
      reason instanceof Error
        ? reason.message
        : "Could not save browser delivery.";
  } finally {
    busy.value = false;
  }
}
onMounted(async () => {
  try {
    apply(await fetchDeploymentSettings());
  } catch (reason) {
    error.value =
      reason instanceof Error
        ? reason.message
        : "Could not load browser delivery.";
  }
});
async function save() {
  if (privateKey.value && configured.value && !confirmed.value) return;
  await perform(
    () =>
      updateDeploymentSettings({
        ...(!settings.value?.provider_locks.web_push_vapid_subject
          ? { web_push_vapid_subject: subject.value || null }
          : {}),
        ...(!locked.value && privateKey.value
          ? { web_push_vapid_private_key: privateKey.value }
          : {}),
      }),
    "Browser delivery settings saved.",
  );
}
</script>

<template>
  <section class="push-settings" aria-label="Server browser delivery">
    <h3>Browser delivery (PWA)</h3>
    <p class="hint">
      Uses the existing PWA plugin and its permission. Configure a server
      contact and a stable application key, then users enroll their browsers
      under Account → Notifications. HTTPS is required outside localhost.
    </p>
    <p v-if="!settings && !error" class="hint">Loading browser delivery…</p>
    <form v-if="settings" @submit.prevent="save">
      <label
        >Server contact{{
          settings.provider_locks.web_push_vapid_subject
            ? " · Managed by ENV"
            : ""
        }}<input
          v-model="subject"
          maxlength="1024"
          placeholder="mailto:admin@example.com or https://example.com/contact"
          :disabled="busy || settings.provider_locks.web_push_vapid_subject"
      /></label>
      <p class="hint">
        An explicit mailto address or HTTPS contact URL for browser push
        services. Your account email is never substituted.
      </p>
      <label
        >Application private key{{ locked ? " · Managed by ENV" : ""
        }}<PasswordInput
          v-model="privateKey"
          mode="replace"
          :disabled="busy || locked"
          :placeholder="
            configured
              ? 'Saved — leave blank to keep it'
              : 'Base64 DER P-256 key, or generate below'
          "
      /></label>
      <p class="hint">
        Saved keys stay encrypted and are never returned to this page. Changing
        or removing the key withdraws existing browsers; users must enroll
        again.
      </p>
      <label v-if="configured && !locked" class="confirmation"
        ><input v-model="confirmed" type="checkbox" :disabled="busy" />I
        understand that replacing or removing the key disables existing browser
        destinations.</label
      >
      <div class="actions">
        <button :disabled="busy || (!!privateKey && configured && !confirmed)">
          Save browser delivery
        </button>
        <button
          v-if="!configured && !locked"
          type="button"
          :disabled="busy || !!privateKey"
          @click="
            perform(
              () => generateBrowserPushKey(),
              'Application key generated. Save a server contact to complete configuration.',
            )
          "
        >
          Generate application key
        </button>
        <button
          v-if="configured && !locked"
          type="button"
          :disabled="busy || !confirmed"
          @click="
            perform(
              () =>
                updateDeploymentSettings({ web_push_vapid_private_key: null }),
              'Application key removed. Existing browsers were disabled.',
            )
          "
        >
          Remove saved key
        </button>
      </div>
      <p class="hint">
        {{
          settings.browser_push?.configured
            ? "Server push configuration is ready."
            : "Server push configuration is incomplete."
        }}
        PWA activation and per-browser consent are also required. Send a test
        from your enrolled browser in Account → Notifications.
      </p>
      <details v-if="settings.browser_push?.public_key">
        <summary>Application public key</summary>
        <code>{{ settings.browser_push.public_key }}</code>
      </details>
    </form>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <p v-if="status" class="success" role="status">{{ status }}</p>
  </section>
</template>

<style scoped>
h3 {
  margin: 0 0 10px;
}
.hint {
  font-size: 13px;
  line-height: 1.6;
  color: var(--ui-dim);
}
label {
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 13px;
}
input {
  color: var(--ui-text);
  background: var(--ui-bg);
  border: 1px solid var(--ui-border-strong);
  border-radius: var(--ui-radius-control);
  padding: 10px;
  font: inherit;
}
.confirmation {
  flex-direction: row;
  align-items: start;
  margin-top: 12px;
  line-height: 1.5;
}
.actions {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin: 14px 0;
}
button {
  border: 0;
  border-radius: var(--ui-radius-control);
  background: var(--ui-accent);
  color: var(--ui-on-accent);
  padding: 10px 14px;
  cursor: pointer;
}
button:disabled {
  opacity: 0.5;
  cursor: default;
}
.error {
  color: var(--ui-error);
}
.success {
  color: var(--ui-accent);
}
code {
  display: block;
  padding: 10px 0;
  overflow-wrap: anywhere;
  font-size: 12px;
}
summary {
  cursor: pointer;
  font-size: 13px;
}
</style>
