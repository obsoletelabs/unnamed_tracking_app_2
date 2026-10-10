<script setup lang="ts">
import { onMounted, ref } from "vue";
import SmtpSettingsSection from "./SmtpSettingsSection.vue";
import BrowserPushSettingsSection from "./BrowserPushSettingsSection.vue";
import { fetchInboxRetentionPolicy } from "../../services/notifications";
import type { InboxRetentionPolicy } from "../../services/notifications";

const policy = ref<InboxRetentionPolicy | null>(null);
const error = ref("");
onMounted(async () => {
  try {
    policy.value = await fetchInboxRetentionPolicy();
  } catch (reason) {
    error.value =
      reason instanceof Error
        ? reason.message
        : "Could not load notification policy.";
  }
});
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
