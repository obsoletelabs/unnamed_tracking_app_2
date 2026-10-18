<script setup lang="ts">
import { onMounted, ref } from "vue";
import {
  capabilityLabels,
  fetchIntegrationGrants,
  reduceIntegrationGrant,
  revokeIntegrationGrant,
  type IntegrationGrant,
} from "../../services/delegation";

const grants = ref<IntegrationGrant[]>([]);
const choices = ref<Record<string, string[]>>({});
const loading = ref(true);
const error = ref("");
const busy = ref("");
function active(grant: IntegrationGrant) {
  return (
    !grant.revoked_at &&
    grant.enabled &&
    (grant.expires_at === null || grant.expires_at > Date.now() / 1000)
  );
}
function date(value: number) {
  return new Date(value * 1000).toLocaleString();
}
async function load() {
  try {
    grants.value = await fetchIntegrationGrants();
    choices.value = Object.fromEntries(
      grants.value.map((grant) => [grant.id, [...grant.scopes]]),
    );
  } catch (err) {
    error.value =
      err instanceof Error ? err.message : "Unable to load connected apps.";
  } finally {
    loading.value = false;
  }
}
async function change(grant: IntegrationGrant, revoke: boolean) {
  busy.value = grant.id;
  error.value = "";
  try {
    if (revoke) await revokeIntegrationGrant(grant.id);
    else await reduceIntegrationGrant(grant.id, choices.value[grant.id] ?? []);
    await load();
  } catch (err) {
    error.value =
      err instanceof Error ? err.message : "Unable to change access.";
  } finally {
    busy.value = "";
  }
}
onMounted(load);
</script>

<template>
  <section class="section">
    <div class="section-header">
      <div>
        <h2>Connected apps</h2>
        <p>Review who can read or change your account data.</p>
      </div>
    </div>
    <p v-if="loading" role="status">Loading connected apps…</p>
    <p v-if="error" role="alert">{{ error }}</p>
    <p v-if="!loading && !grants.length">
      No apps or integrations have access to your account. You can approve one
      when connecting from Playnite or an Activity.
    </p>
    <article v-for="grant in grants" :key="grant.id" class="grant">
      <h3>{{ grant.name }}</h3>
      <p v-if="grant.kind === 'desktop'" class="origin">
        Desktop app<template v-if="grant.external_account">
          on {{ grant.external_account.username }}</template
        >
      </p>
      <p v-else class="origin">{{ grant.origin }}</p>
      <template v-if="grant.kind === 'desktop'" />
      <p v-else-if="grant.external_account">
        <strong>Linked account: {{ grant.external_account.username }}</strong
        ><br />Account ID: {{ grant.external_account.subject }} · Reported by
        {{ grant.name }}.
      </p>
      <p v-else>
        Linked account label unavailable for this older grant. Reconnect to
        include it.
      </p>
      <p>
        {{
          grant.revoked_at
            ? "Revoked"
            : active(grant)
              ? "Active"
              : "Expired or disabled"
        }}
        ·
        {{
          grant.expires_at === null
            ? "Until revoked"
            : "Expires " + date(grant.expires_at)
        }}
      </p>
      <fieldset :disabled="!active(grant) || busy === grant.id">
        <legend>Granted access</legend>
        <label v-for="scope in grant.scopes" :key="scope"
          ><input v-model="choices[grant.id]" type="checkbox" :value="scope" />
          {{ capabilityLabels[scope] ?? scope }}</label
        >
      </fieldset>
      <div v-if="active(grant)" class="actions">
        <button
          :disabled="busy === grant.id || !choices[grant.id]?.length"
          @click="change(grant, false)"
        >
          Save reduced access
        </button>
        <button :disabled="busy === grant.id" @click="change(grant, true)">
          Revoke access
        </button>
      </div>
    </article>
    <p>
      Changes apply to subsequent requests. Adding new capabilities requires a
      fresh approval. Revoking an integration leaves your account, library and
      other connections intact.
    </p>
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
  margin: 0 0 8px;
  font-size: 1rem;
}
p {
  color: color-mix(in srgb, var(--ui-text) 68%, transparent);
  line-height: 1.5;
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
button:last-child {
  background: transparent;
  color: var(--ui-error);
  border: 1px solid var(--ui-border);
}
button:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}
input[type="checkbox"] {
  accent-color: var(--ui-accent);
  width: 18px;
  height: 18px;
}
fieldset {
  border: 1px solid var(--ui-border);
  border-radius: var(--ui-radius-control);
}
fieldset label {
  display: flex;
  align-items: center;
  gap: 8px;
}
.grant {
  border: 1px solid var(--ui-border);
  border-radius: 12px;
  padding: 1rem;
  margin: 1rem 0;
}
.origin {
  overflow-wrap: anywhere;
  color: color-mix(in srgb, var(--ui-text) 60%, transparent);
}
fieldset {
  display: grid;
  gap: 0.75rem;
  padding: 1rem;
  margin: 1rem 0;
}
.actions {
  display: flex;
  flex-wrap: wrap;
  gap: 0.75rem;
}
button {
  min-height: 44px;
  padding: 0.65rem 1rem;
}
</style>
