<script setup lang="ts">
import { onMounted, ref } from "vue";
import {
  fetchIntegrationPolicy,
  saveIntegrationClient,
  saveIntegrationPolicy,
  type IntegrationPolicy,
  type IntegrationClient,
} from "../../services/delegation";
const policy = ref<IntegrationPolicy | null>(null);
const name = ref("");
const origin = ref("");
const error = ref("");
const message = ref("");
const busy = ref(false);
async function load() {
  try {
    policy.value = await fetchIntegrationPolicy();
  } catch (err) {
    error.value =
      err instanceof Error ? err.message : "Unable to load integration policy.";
  }
}
async function save() {
  if (!policy.value) return;
  busy.value = true;
  error.value = "";
  message.value = "";
  try {
    await saveIntegrationPolicy({
      enabled: policy.value.enabled,
      allow_guests: policy.value.allow_guests,
      allow_any_origin: policy.value.allow_any_origin,
      allow_legacy_api_keys: policy.value.allow_legacy_api_keys,
      allow_desktop_apps: policy.value.allow_desktop_apps,
      guest_retention_hours: policy.value.guest_retention_hours,
      maximum_grant_hours: policy.value.maximum_grant_hours,
    });
    message.value = "Instance policy saved.";
  } catch (err) {
    error.value = err instanceof Error ? err.message : "Unable to save policy.";
  } finally {
    busy.value = false;
  }
}
async function saveClient(client?: IntegrationClient) {
  busy.value = true;
  error.value = "";
  try {
    await saveIntegrationClient(
      client
        ? { name: client.name, origin: client.origin, enabled: !client.enabled }
        : { name: name.value, origin: origin.value, enabled: true },
    );
    name.value = "";
    origin.value = "";
    await load();
  } catch (err) {
    error.value =
      err instanceof Error ? err.message : "Unable to save integration.";
  } finally {
    busy.value = false;
  }
}
onMounted(load);
</script>

<template>
  <section class="section">
    <div class="section-header">
      <div>
        <h2>Integration access</h2>
        <p>
          Choose which Activities and integrations may request access to this
          instance.
        </p>
      </div>
    </div>
    <p v-if="error" role="alert">{{ error }}</p>
    <p v-if="message" role="status">{{ message }}</p>
    <template v-if="policy">
      <form class="policy" @submit.prevent="save">
        <label
          ><input v-model="policy.allow_desktop_apps" type="checkbox" /> Let
          people connect desktop apps such as Playnite</label
        >
        <p>
          Desktop apps sign in through each person's browser, ask only for what
          they sync and show up in Connected apps. This is separate from web
          integrations below and replaces pasting an API key.
        </p>
        <label
          ><input v-model="policy.enabled" type="checkbox" /> Enable
          user-delegated web integrations</label
        >
        <label
          ><input v-model="policy.allow_guests" type="checkbox" /> Allow guests
          in hosted Activities</label
        >
        <label
          >Guest data retention (hours)<input
            v-model.number="policy.guest_retention_hours"
            type="number"
            min="1"
            max="8760"
            required
        /></label>
        <p>
          Guest means no connected tracking library, even though the participant
          signs in through Discord. Keep guest/room data for up to 365 days
          (8760 hours). Saved account grants have their own expiry or Until
          revoked setting.
        </p>
        <label
          ><input v-model="policy.allow_legacy_api_keys" type="checkbox" />
          Allow unsafe legacy API-key connections from older instances</label
        >
        <p>
          Legacy keys may grant unrestricted account access. The Activity broker
          uses them only for identity and library reads. Users must explicitly
          acknowledge this and can disconnect or revoke the key on their
          instance.
        </p>
        <label
          >Maximum timed access duration (hours)<input
            v-model.number="policy.maximum_grant_hours"
            type="number"
            min="1"
            max="720"
            required
        /></label>
        <label
          ><input v-model="policy.allow_any_origin" type="checkbox" /> Allow
          consent requests from any HTTPS integration origin</label
        >
        <p>
          Each user must still approve access. Individually disabled origins
          stay blocked.
        </p>
        <button :disabled="busy">Save instance policy</button>
      </form>
      <p>
        Owner approval lets an integration request access. Each user must still
        approve their own data and capabilities. Guest participation never
        creates an account.
      </p>
      <h3>Approved apps and integration origins</h3>
      <article v-for="client in policy.clients" :key="client.id" class="client">
        <strong>{{ client.name }}</strong
        ><span>{{
          client.kind === "desktop" ? "Desktop app" : client.origin
        }}</span
        ><span>{{ client.enabled ? "Allowed" : "Disabled" }}</span>
        <button :disabled="busy" @click="saveClient(client)">
          {{ client.enabled ? "Disable" : "Allow" }}
        </button>
      </article>
      <form class="policy" @submit.prevent="saveClient()">
        <label
          >Integration name<input v-model="name" maxlength="100" required
        /></label>
        <label
          >HTTPS origin<input
            v-model="origin"
            type="url"
            placeholder="https://activity.example.com"
            required
        /></label>
        <button :disabled="busy">Allow this integration</button>
      </form>
      <p>
        Only allow an operator you trust. Disabling an origin or integrations
        blocks existing grants on their next request.
      </p>
    </template>
  </section>
</template>

<style scoped>
.section {
  color: var(--ui-text);
}
.section-header {
  margin-bottom: 24px;
}
h2 {
  margin: 0 0 12px;
  font: var(--ui-weight-heading) var(--ui-font-heading)/1.4
    var(--ui-font-family);
}
h3 {
  margin: 24px 0 12px;
  font-size: 1rem;
}
p {
  color: color-mix(in srgb, var(--ui-text) 68%, transparent);
  line-height: 1.5;
}
input {
  background: var(--ui-bg);
  color: var(--ui-text);
  border: 1px solid var(--ui-border);
  border-radius: var(--ui-radius-control);
  font: inherit;
}
input[type="checkbox"] {
  accent-color: var(--ui-accent);
  width: 18px;
  height: 18px;
  flex: none;
}
input:focus-visible,
button:focus-visible {
  outline: 2px solid var(--ui-accent);
  outline-offset: 3px;
}
button {
  border: 0;
  border-radius: var(--ui-radius-control);
  background: var(--ui-accent);
  color: var(--ui-on-accent);
  font: inherit;
  font-weight: 600;
  cursor: pointer;
}
button:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}
.policy {
  display: grid;
  gap: 1rem;
  max-width: 650px;
  margin: 1rem 0 1.5rem;
}
.policy label {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.75rem;
}
.policy input:not([type="checkbox"]) {
  flex: 1;
  min-width: 180px;
  padding: 0.65rem;
}
button {
  min-height: 44px;
  padding: 0.65rem 1rem;
  width: fit-content;
}
.client {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.75rem;
  margin: 1rem 0;
  padding: 1rem;
  border: 1px solid var(--ui-border);
  border-radius: 10px;
}
.client span {
  overflow-wrap: anywhere;
}
</style>
