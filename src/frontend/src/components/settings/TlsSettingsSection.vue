<script setup lang="ts">
import { onMounted, ref } from "vue";
import ToggleButton from "./ToggleButton.vue";
import {
  fetchDeploymentSettings,
  updateDeploymentSettings,
} from "../../services/deploymentSettings";
import type { DeploymentSettings } from "../../services/deploymentSettings";

const settings = ref<DeploymentSettings | null>(null);
const enabled = ref(false);
const redirect = ref(false);
const certificate = ref("");
const key = ref("");
const busy = ref(false);
const error = ref("");
const status = ref("");
function apply(result: DeploymentSettings) {
  settings.value = result;
  enabled.value = result.nginx?.enabled ?? false;
  redirect.value = result.nginx?.redirect_http ?? false;
  certificate.value = result.nginx?.certificate ?? "";
  key.value = result.nginx?.private_key ?? "";
}
onMounted(async () => {
  try {
    apply(await fetchDeploymentSettings());
  } catch (reason) {
    error.value =
      reason instanceof Error ? reason.message : "Could not load TLS settings.";
  }
});
function setEnabled(value: boolean) {
  enabled.value = value;
  if (!value && !settings.value?.nginx?.locked.redirect_http)
    redirect.value = false;
}
async function save() {
  busy.value = true;
  error.value = "";
  status.value = "";
  try {
    const payload: Record<string, string | boolean> = { reload_nginx: true };
    const values = {
      enabled: enabled.value,
      redirect_http: redirect.value,
      certificate: certificate.value,
      private_key: key.value,
    };
    for (const [field, value] of Object.entries(values)) {
      if (!settings.value?.nginx?.locked[field])
        payload[`nginx_tls_${field}`] = value;
    }
    apply(await updateDeploymentSettings(payload));
    status.value = settings.value?.nginx?.runtime_available
      ? "TLS settings validated and Nginx reloaded."
      : "TLS settings saved for the production container. This development server does not run Nginx.";
  } catch (reason) {
    error.value =
      reason instanceof Error
        ? reason.message
        : "Could not apply TLS settings.";
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <section class="tls-settings" aria-label="Production TLS settings">
    <h3>HTTPS / TLS</h3>
    <p class="hint">
      Configure the production container's Nginx listener. Saving validates the
      candidate and reloads Nginx immediately. Invalid certificates or
      configuration leave the working settings active.
    </p>
    <form v-if="settings" @submit.prevent="save()">
      <div class="tls-block">
        <ToggleButton
          :model-value="enabled"
          label="Enable production HTTPS"
          :disabled="busy || settings.nginx?.locked.enabled"
          @update:model-value="setEnabled"
          >Enable production HTTPS{{
            settings.nginx?.locked.enabled ? " · Managed by ENV" : ""
          }}</ToggleButton
        >
        <p class="hint">
          Publish the container's port 443 before using HTTPS. Reverse-proxy TLS
          termination can continue with this listener disabled.
        </p>
      </div>
      <div class="tls-block">
        <ToggleButton
          v-model="redirect"
          label="Redirect HTTP to HTTPS"
          :disabled="busy || !enabled || settings.nginx?.locked.redirect_http"
          >Redirect HTTP to HTTPS{{
            settings.nginx?.locked.redirect_http ? " · Managed by ENV" : ""
          }}</ToggleButton
        >
        <p class="hint">
          Enable after confirming HTTPS is reachable on your chosen domain and
          published port.
        </p>
      </div>
      <div class="tls-block">
        <label
          >TLS certificate file path{{
            settings.nginx?.locked.certificate ? " · Managed by ENV" : ""
          }}<input
            v-model="certificate"
            maxlength="512"
            placeholder="/etc/nginx/tls/tls.crt"
            :disabled="busy || settings.nginx?.locked.certificate"
        /></label>
        <label
          >TLS private key file path{{
            settings.nginx?.locked.private_key ? " · Managed by ENV" : ""
          }}<input
            v-model="key"
            maxlength="512"
            placeholder="/etc/nginx/tls/tls.key"
            :disabled="busy || settings.nginx?.locked.private_key"
        /></label>
        <p class="hint">
          Use absolute paths to read-only mounted files; key contents are never
          uploaded. Leave both blank to use the default mounted pair or a
          localhost self-signed certificate. Public domains need a matching
          trusted certificate.
        </p>
      </div>
      <p v-if="!settings.nginx?.runtime_available" class="hint">
        This development server does not run production Nginx; saved settings
        apply in the production container.
      </p>
      <div class="actions">
        <button :disabled="busy">
          {{ busy ? "Applying…" : "Save TLS settings" }}
        </button>
      </div>
    </form>
    <p v-if="status" class="success" role="status">{{ status }}</p>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
  </section>
</template>

<style scoped>
.tls-settings {
  margin: 24px 0;
  border-top: 1px solid var(--ui-border);
  padding-top: 4px;
}
h3 {
  margin: 20px 0 12px;
}
.tls-block {
  margin: 12px 0;
  padding: 14px;
  border: 1px solid var(--ui-border);
  border-radius: var(--ui-radius-control);
  display: flex;
  flex-direction: column;
  gap: 12px;
}
label {
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 13px;
}
input {
  min-width: 0;
  background: var(--ui-bg);
  color: var(--ui-text);
  padding: 10px;
  border: 1px solid var(--ui-border-strong);
  border-radius: var(--ui-radius-control);
  font: inherit;
}
.hint {
  color: var(--ui-dim);
  font-size: 13px;
  line-height: 1.6;
  margin: 0;
}
.actions {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
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
.success {
  color: var(--ui-good);
}
.error {
  color: var(--ui-error);
}
</style>
