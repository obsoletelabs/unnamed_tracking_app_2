<script setup lang="ts">
import { onMounted, reactive, ref } from "vue";
import PasswordInput from "../PasswordInput.vue";
import {
  fetchDeploymentSettings,
  updateDeploymentSettings,
} from "../../services/deploymentSettings";
import type { DeploymentSettings } from "../../services/deploymentSettings";

const fields = [
  ["smtp_host", "SMTP host"],
  ["smtp_port", "SMTP port"],
  ["smtp_from_address", "Sender email address"],
  ["smtp_username", "SMTP username"],
  ["smtp_password", "SMTP password"],
  ["smtp_tls_mode", "Transport security"],
] as const;
const settings = ref<DeploymentSettings | null>(null);
const values = reactive<Record<string, string>>({
  smtp_port: "587",
  smtp_tls_mode: "starttls",
});
const dirty = reactive<Record<string, boolean>>({});
const error = ref("");
const saved = ref(false);
const busy = ref(false);

function apply(result: DeploymentSettings) {
  settings.value = result;
  for (const [key] of fields) {
    const value = result.providers[key];
    values[key] =
      value !== null && value !== undefined
        ? String(value)
        : key === "smtp_port"
          ? "587"
          : key === "smtp_tls_mode"
            ? "starttls"
            : "";
    delete dirty[key];
  }
  values.smtp_password = "";
  if (result.provider_locks.smtp_tls_mode && result.smtp)
    values.smtp_tls_mode = result.smtp.tls_mode;
  if (result.provider_locks.smtp_port) values.smtp_port = "";
}
onMounted(async () => {
  try {
    apply(await fetchDeploymentSettings());
  } catch (reason) {
    error.value =
      reason instanceof Error
        ? reason.message
        : "Could not load SMTP settings.";
  }
});
async function save() {
  busy.value = true;
  error.value = "";
  saved.value = false;
  try {
    const payload: Record<string, string | number | null> = {};
    for (const [key] of fields) {
      if (!dirty[key] || settings.value?.provider_locks[key]) continue;
      if (key === "smtp_password" && !values[key]) continue;
      payload[key] =
        key === "smtp_port" ? Number(values[key]) : values[key] || null;
    }
    apply(await updateDeploymentSettings(payload));
    saved.value = true;
  } catch (reason) {
    error.value =
      reason instanceof Error
        ? reason.message
        : "Could not save SMTP settings.";
  } finally {
    busy.value = false;
  }
}
async function clearPassword() {
  busy.value = true;
  error.value = "";
  try {
    apply(await updateDeploymentSettings({ smtp_password: null }));
  } catch (reason) {
    error.value =
      reason instanceof Error
        ? reason.message
        : "Could not remove SMTP password.";
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <section class="smtp-section">
    <h3>Email delivery (SMTP)</h3>
    <p class="hint">
      Built-in email delivery for personal destinations. Users add and verify
      addresses under Notifications. Deployment ENV values take precedence and
      are locked.
    </p>
    <p v-if="!settings && !error" class="hint">Loading SMTP settings…</p>
    <form v-if="settings" @submit.prevent="save">
      <div class="grid">
        <label v-for="[key, label] in fields" :key="key">
          <span
            >{{ label
            }}{{
              settings.provider_locks[key] ? " · Managed by ENV" : ""
            }}</span
          >
          <PasswordInput
            v-if="key === 'smtp_password'"
            :model-value="values[key] ?? ''"
            mode="replace"
            :disabled="busy || settings.provider_locks[key]"
            :placeholder="
              settings.providers.smtp_password_configured
                ? 'Saved — enter a value to replace it'
                : ''
            "
            @update:model-value="
              values[key] = $event;
              dirty[key] = true;
            "
          />
          <select
            v-else-if="key === 'smtp_tls_mode'"
            :aria-label="label"
            v-model="values[key]"
            :disabled="busy || settings.provider_locks[key]"
            @change="dirty[key] = true"
          >
            <option value="starttls">STARTTLS</option>
            <option value="ssl">Implicit TLS</option>
            <option value="none">Plaintext</option>
          </select>
          <input
            v-else
            v-model="values[key]"
            :type="
              key === 'smtp_port'
                ? 'number'
                : key === 'smtp_from_address'
                  ? 'email'
                  : 'text'
            "
            :min="key === 'smtp_port' ? 1 : undefined"
            :max="key === 'smtp_port' ? 65535 : undefined"
            :disabled="busy || settings.provider_locks[key]"
            :placeholder="
              settings.provider_locks[key]
                ? 'Managed by deployment environment'
                : ''
            "
            @input="dirty[key] = true"
          />
        </label>
      </div>
      <button
        v-if="
          settings.providers.smtp_password_configured &&
          !settings.provider_locks.smtp_password
        "
        type="button"
        :disabled="busy"
        @click="clearPassword"
      >
        Remove stored SMTP password
      </button>
      <p class="hint">
        STARTTLS and implicit TLS verify the server certificate. Choose the
        matching port for your SMTP server. Critical urgency adds high-priority
        headers; mail clients decide how to alert.
      </p>
      <p v-if="values.smtp_tls_mode === 'none'" class="warning" role="status">
        Plaintext SMTP exposes email contents and credentials in transit.
        Security, recovery and verification messages are allowed only in
        development mode or when the configured server is a literal local IP.
      </p>
      <p class="hint">
        Password reset and invite features are plugins; SMTP itself is built in.
      </p>
      <p v-if="saved" class="success" role="status">SMTP settings saved.</p>
      <button :disabled="busy">
        {{ busy ? "Saving…" : "Save SMTP settings" }}
      </button>
    </form>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
  </section>
</template>

<style scoped>
.smtp-section {
  padding-bottom: 18px;
  border-bottom: 1px solid var(--ui-border);
}
h3 {
  margin: 0 0 10px;
}
.grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
}
label {
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 13px;
}
input,
select {
  color: var(--ui-text);
  background: var(--ui-bg);
  border: 1px solid var(--ui-border-strong);
  border-radius: var(--ui-radius-control);
  padding: 10px;
  font: inherit;
  min-width: 0;
}
.hint,
.warning,
.error,
.success {
  font-size: 13px;
  line-height: 1.6;
  color: var(--ui-dim);
}
.warning {
  color: var(--ui-warning);
}
.error {
  color: var(--ui-error);
}
.success {
  color: var(--ui-good);
}
button {
  background: var(--ui-accent);
  color: var(--ui-on-accent);
  border: 0;
  border-radius: var(--ui-radius-control);
  padding: 10px 14px;
  font-weight: 600;
  cursor: pointer;
}
button:disabled {
  opacity: 0.55;
}
@media (max-width: 760px) {
  .grid {
    grid-template-columns: 1fr;
  }
}
</style>
