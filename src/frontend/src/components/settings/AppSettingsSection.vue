<script setup lang="ts">
import { onMounted, ref } from "vue";
import TrustedProxyControls from "./TrustedProxyControls.vue";
import {
  fetchDeploymentSettings,
  updateDeploymentSettings,
} from "../../services/deploymentSettings";
import type { DeploymentSettings } from "../../services/deploymentSettings";

const settings = ref<DeploymentSettings | null>(null);
const url = ref("");
const header = ref("");
const proxies = ref("");
const busy = ref(false);
const error = ref("");
const saved = ref(false);
function apply(result: DeploymentSettings) {
  settings.value = result;
  url.value = result.app?.public_app_url ?? "";
  header.value = result.real_ip.header;
  proxies.value = result.real_ip.trusted_proxies;
}
onMounted(async () => {
  try {
    apply(await fetchDeploymentSettings());
  } catch (reason) {
    error.value =
      reason instanceof Error ? reason.message : "Could not load app settings.";
  }
});
async function save() {
  busy.value = true;
  saved.value = false;
  error.value = "";
  try {
    const payload: Record<string, string | null> = {};
    if (!settings.value?.app?.url_locked)
      payload.public_app_url = url.value || null;
    if (!settings.value?.real_ip.locked.header)
      payload.nginx_realip_header = header.value;
    if (!settings.value?.real_ip.locked.trusted_proxies)
      payload.nginx_realip_trusted_proxies = proxies.value;
    apply(await updateDeploymentSettings(payload));
    saved.value = true;
  } catch (reason) {
    error.value =
      reason instanceof Error ? reason.message : "Could not save app settings.";
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <section class="app-settings">
    <h2>Application</h2>
    <p class="hint">
      App URLs and production reverse-proxy configuration. These settings are
      independent of OIDC sign-in callbacks.
    </p>
    <p v-if="!settings && !error" class="hint">Loading app settings…</p>
    <form v-if="settings" @submit.prevent="save">
      <h3>App URL</h3>
      <label
        >Shared public app URL{{
          settings.app?.url_locked ? " · Managed by ENV" : ""
        }}<input
          v-model="url"
          type="url"
          maxlength="2048"
          placeholder="https://tracking.example.com"
          :disabled="busy || settings.app?.url_locked"
      /></label>
      <p class="hint">
        Optional HTTPS URL with a public fully qualified domain name.
        PUBLIC_APP_URL takes precedence over this saved value. Notification
        preferences take precedence over both; otherwise links fall back to each
        user's last-used app origin.
      </p>
      <p class="hint">
        Your last-used app URL:
        {{ settings.app?.last_app_url || "Not detected yet" }}
      </p>
      <p class="hint">
        <router-link to="/settings?section=notifications"
          >Your notification URL preferences</router-link
        >
        can use separate URLs for email and other destinations.
      </p>
      <section class="proxy-section">
        <h3>Client IP / reverse proxy</h3>
        <p class="hint">
          Nginx trusts loopback by default. Add only networks that actually
          proxy requests for this deployment. Environment settings are locked.
        </p>
        <label
          >Real client IP header<input
            v-model="header"
            :disabled="busy || settings.real_ip.locked.header"
        /></label>
        <TrustedProxyControls
          v-model="proxies"
          :disabled="busy || settings.real_ip.locked.trusted_proxies"
        />
      </section>
      <p v-if="saved" class="success" role="status">App settings saved.</p>
      <button :disabled="busy">
        {{ busy ? "Saving…" : "Save app settings" }}
      </button>
    </form>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
  </section>
</template>

<style scoped>
h2 {
  margin: 0 0 12px;
  font: var(--ui-weight-heading) var(--ui-font-heading)/1.4
    var(--ui-font-family);
}
h3 {
  margin: 20px 0 12px;
}
label {
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 13px;
}
input {
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
}
.proxy-section {
  margin: 20px 0;
  padding-top: 4px;
  border-top: 1px solid var(--ui-border);
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
.error {
  color: var(--ui-error);
}
.success {
  color: var(--ui-good);
}
</style>
