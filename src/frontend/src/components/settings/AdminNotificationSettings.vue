<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useRoute } from "vue-router";
import SmtpSettingsSection from "./SmtpSettingsSection.vue";
import BrowserPushSettingsSection from "./BrowserPushSettingsSection.vue";
import PluginProviderConfigurationForm from "./PluginProviderConfigurationForm.vue";
import {
  fetchInboxRetentionPolicy,
  fetchNotificationRouting,
  configurePluginNotificationServer,
} from "../../services/notifications";
import type {
  InboxRetentionPolicy,
  NotificationRoutingProvider,
} from "../../services/notifications";
import type { UiValue } from "../../services/pluginUi";

const policy = ref<InboxRetentionPolicy | null>(null);
const error = ref("");
const status = ref("");
const busy = ref(false);
const providers = ref<NotificationRoutingProvider[]>([]);
const route = useRoute();
const serverProviders = computed(() =>
  providers.value
    .filter(
      (p) => p.configuration_available && p.definition?.server_fields.length,
    )
    .sort(
      (a, b) =>
        Number(b.server_configured) - Number(a.server_configured) ||
        a.name.localeCompare(b.name),
    ),
);
async function reloadProviders() {
  providers.value = (await fetchNotificationRouting()).providers;
}
onMounted(async () => {
  try {
    [policy.value] = await Promise.all([
      fetchInboxRetentionPolicy(),
      reloadProviders(),
    ]);
  } catch (reason) {
    error.value =
      reason instanceof Error
        ? reason.message
        : "Could not load notification policy.";
  }
});
async function configure(
  provider: NotificationRoutingProvider,
  values: Record<string, UiValue>,
) {
  busy.value = true;
  error.value = status.value = "";
  try {
    await configurePluginNotificationServer(provider.id, values);
    status.value = `${provider.name} server configuration saved. Users must explicitly configure their destinations again.`;
  } catch (reason) {
    error.value =
      reason instanceof Error
        ? reason.message
        : "Could not save provider configuration.";
  } finally {
    await reloadProviders().catch(() => {});
    busy.value = false;
  }
}
</script>

<template>
  <section class="admin-notifications">
    <h2>Server notifications</h2>
    <p class="hint">
      Configure delivery for this server. Personal addresses, destinations and
      routing are under
      <router-link to="/settings?section=notifications"
        >Account → Notifications</router-link
      >.
    </p>
    <SmtpSettingsSection />
    <BrowserPushSettingsSection />
    <section>
      <h3>Plugin providers</h3>
      <p class="hint">
        Install and grant notification providers under
        <router-link to="/settings?section=plugins">Plugins</router-link>. Their
        configuration appears when installed. Plugins cannot lower destination
        trust requirements.
      </p>
      <p v-if="status" class="hint" role="status">{{ status }}</p>
      <details
        v-for="provider in serverProviders"
        :key="provider.id"
        :open="route?.query.notification_provider === provider.id"
        class="provider"
      >
        <summary>
          {{ provider.name }} ·
          {{ provider.server_configured ? "Configured" : "Set up" }}
        </summary>
        <p class="hint">
          The plugin stores these credentials. Saving changes disables its
          existing destinations until users configure them again. Host SMTP
          credentials are never shared.
        </p>
        <PluginProviderConfigurationForm
          :fields="provider.definition!.server_fields"
          :editing="provider.server_configured"
          :busy="busy"
          @save="configure(provider, $event)"
        />
      </details>
    </section>
    <section>
      <h3>Inbox retention</h3>
      <p v-if="policy" class="hint">
        Server default:
        {{ policy.default_days ? `${policy.default_days} days` : "Unlimited" }}.
        Maximum:
        {{
          policy.maximum_days ? `${policy.maximum_days} days` : "No maximum"
        }}. Users choose their own duration within this limit.
      </p>
      <p class="hint">
        Set <code>NOTIFICATION_RETENTION_DEFAULT_DAYS</code> and
        <code>NOTIFICATION_RETENTION_MAXIMUM_DAYS</code> in deployment
        configuration. Zero means unlimited/no maximum. Restart after changing
        ENV values. Dedupe receipts are retained separately from inbox content.
      </p>
      <p v-if="error" class="error" role="alert">{{ error }}</p>
    </section>
  </section>
</template>

<style scoped>
.provider {
  padding: 12px;
  border: 1px solid var(--ui-border);
  border-radius: var(--ui-radius-control);
  margin-block: 12px;
  min-width: 0;
}
summary {
  cursor: pointer;
  overflow-wrap: anywhere;
}
.admin-notifications {
  display: flex;
  flex-direction: column;
  gap: 20px;
}
h2,
h3 {
  margin: 0 0 10px;
}
.hint,
.error {
  font-size: 13px;
  line-height: 1.6;
  color: var(--ui-dim);
}
.error {
  color: var(--ui-error);
}
code {
  overflow-wrap: anywhere;
}
</style>
