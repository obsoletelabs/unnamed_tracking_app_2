<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import PasswordInput from "../PasswordInput.vue";
import { currentUser } from "../../state/auth";
import {
  fetchMetadataProviders,
  saveMetadataProviderConfiguration,
} from "../../services/metadata";
import type { MetadataProviderStatus } from "../../services/metadata";

const props = defineProps<{ pluginId?: string }>();
const providers = ref<MetadataProviderStatus[]>([]);
const scope = ref<"user" | "system">("user");
const values = ref<Record<string, Record<string, string>>>({});
const busy = ref<string | null>(null);
const error = ref<string | null>(null);
const message = ref<string | null>(null);
const shown = computed(() =>
  providers.value.filter(
    (provider) => !props.pluginId || provider.plugin_id === props.pluginId,
  ),
);
const states: Record<string, string> = {
  disabled: "Disabled",
  not_configured: "Not configured",
  unvalidated: "Awaiting validation",
  healthy: "Healthy",
  temporarily_unavailable: "Temporarily unavailable",
  rate_limited: "Rate limited",
  plugin_unavailable: "Plugin unavailable",
  invalid_configuration: "Invalid configuration",
};
function fields(provider: MetadataProviderStatus) {
  return provider.configuration.filter(
    (field) => field.scope === scope.value || field.scope === "both",
  );
}
async function refresh() {
  try {
    providers.value = await fetchMetadataProviders();
    for (const provider of providers.value)
      values.value[provider.provider_id] ??= {};
  } catch (failure) {
    error.value =
      failure instanceof Error
        ? failure.message
        : "Provider status is unavailable.";
  }
}
async function save(provider: MetadataProviderStatus, clear = false) {
  busy.value = provider.provider_id;
  error.value = null;
  message.value = null;
  try {
    const patch: Record<string, string | null> = {};
    for (const field of fields(provider)) {
      const value = values.value[provider.provider_id]?.[field.key];
      if (clear) patch[field.key] = null;
      else if (value) patch[field.key] = value;
    }
    await saveMetadataProviderConfiguration(
      provider.provider_id,
      scope.value,
      patch,
    );
    values.value[provider.provider_id] = {};
    message.value =
      "Saved. Validation runs in the background; refresh status to see its result.";
    await refresh();
  } catch (failure) {
    error.value =
      failure instanceof Error
        ? failure.message
        : "Provider settings could not be saved.";
  } finally {
    busy.value = null;
  }
}
onMounted(() => void refresh());
</script>

<template>
  <section class="provider-settings">
    <h3>Metadata provider plugins</h3>
    <p>
      Install providers through Plugin Manager. Credentials are encrypted; saved
      secrets stay hidden.
    </p>
    <label v-if="currentUser?.is_admin">
      Configuration owner
      <select v-model="scope">
        <option value="user">My account</option>
        <option value="system">System default</option>
      </select>
    </label>
    <p v-if="!shown.length">No metadata provider plugins are registered.</p>
    <article
      v-for="provider in shown"
      :key="provider.provider_id"
      class="provider"
    >
      <strong>{{ provider.name }}</strong>
      <p role="status">{{ states[provider.state] ?? provider.state }}</p>
      <p>
        {{ provider.media_types.join(", ") }} ·
        {{
          Object.entries(provider.operations)
            .filter(([, action]) => action)
            .map(([name]) => name)
            .join(", ")
        }}
      </p>
      <p v-if="provider.checked_at">
        Last validated:
        {{ new Date(provider.checked_at * 1000).toLocaleString() }}
      </p>
      <p v-if="provider.failure">
        Validation result: {{ states[provider.state] ?? "Unavailable" }}
      </p>
      <template v-if="fields(provider).length">
        <label v-for="field in fields(provider)" :key="field.key">
          {{ field.label }}<span v-if="field.required"> (required)</span>
          <small>{{
            provider.configured_fields[field.key]?.[scope]
              ? "Saved in this scope"
              : "No saved value in this scope"
          }}</small>
          <PasswordInput
            v-if="field.secret"
            v-model="values[provider.provider_id]![field.key]"
            autocomplete="new-password"
            placeholder="Leave blank to retain the saved value"
          />
          <input
            v-else
            v-model="values[provider.provider_id]![field.key]"
            type="text"
            autocomplete="off"
            placeholder="Leave blank to retain the saved value"
          />
        </label>
        <button type="button" :disabled="busy !== null" @click="save(provider)">
          Save configuration
        </button>
        <button
          type="button"
          :disabled="busy !== null"
          @click="save(provider, true)"
        >
          Clear this scope
        </button>
      </template>
    </article>
    <button type="button" :disabled="busy !== null" @click="refresh">
      Refresh provider status
    </button>
    <p v-if="message" role="status">{{ message }}</p>
    <p v-if="error" role="alert">{{ error }}</p>
  </section>
</template>

<style scoped>
.provider-settings {
  display: grid;
  gap: 0.75rem;
}
.provider {
  border: 1px solid var(--color-border);
  border-radius: 0.5rem;
  padding: 1rem;
}
label {
  display: grid;
  gap: 0.35rem;
  margin-block: 0.5rem;
}
button {
  margin-inline-end: 0.5rem;
}
</style>
