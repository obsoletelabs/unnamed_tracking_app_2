<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import PasswordInput from "../PasswordInput.vue";
import { currentUser } from "../../state/auth";
import { SOURCE_VISUALS } from "../../composables/useMetadataSources";
import {
  fetchMetadataProviders,
  saveMetadataProviderConfiguration,
  fetchServerAutomaticSearch,
  saveServerAutomaticSearch,
} from "../../services/metadata";
import type { MetadataProviderStatus } from "../../services/metadata";
import { PROVIDER_STATE_LABELS as states } from "../../utils/metadataProviders";
import { preferences, preferencesLoaded } from "../../state/preferences";
import { queuePreferences } from "../../services/preferences";

const props = defineProps<{
  pluginId?: string;
  initialScope?: "user" | "system";
}>();
const providers = ref<MetadataProviderStatus[]>([]);
const scope = ref<"user" | "system">(props.initialScope ?? "user");
const values = ref<Record<string, Record<string, string>>>({});
const busy = ref<string | null>(null);
const error = ref<string | null>(null);
const message = ref<string | null>(null);
const expanded = ref<Record<string, boolean>>({});
const loading = ref(true);
const serverAutomaticSearch = ref(true);
function accountEnabled(provider: MetadataProviderStatus) {
  return (
    preferences.value.metadata_providers_enabled?.[provider.provider_id] ??
    provider.account_enabled ??
    true
  );
}
async function changeProviderEnabled(
  provider: MetadataProviderStatus,
  input: HTMLInputElement,
) {
  const enabled = input.checked;
  input.checked = accountEnabled(provider);
  const accountId = currentUser.value?.id;
  busy.value = provider.provider_id;
  error.value = null;
  message.value = null;
  try {
    const result = await queuePreferences({
      metadata_providers_enabled: {
        ...preferences.value.metadata_providers_enabled,
        [provider.provider_id]: enabled,
      },
    });
    if (currentUser.value?.id !== accountId) return;
    if (result.latest) preferences.value = result.prefs;
    await refresh();
    message.value = "Saved";
  } catch (failure) {
    if (currentUser.value?.id !== accountId) return;
    error.value =
      failure instanceof Error
        ? failure.message
        : "Provider choice could not be saved.";
  } finally {
    busy.value = null;
  }
}
const masterAutomaticSearch = computed(() =>
  scope.value === "system"
    ? serverAutomaticSearch.value
    : preferences.value.metadata_automatic_search_enabled,
);
function providerAutomaticSearch(provider: MetadataProviderStatus) {
  return scope.value === "system"
    ? (provider.automatic_search?.server_provider_enabled ?? true)
    : (preferences.value.metadata_automatic_search_providers[
        provider.provider_id
      ] ?? true);
}
async function changeAutomaticSearch(
  enabled: boolean,
  provider?: MetadataProviderStatus,
) {
  const accountId = currentUser.value?.id;
  busy.value = provider?.provider_id ?? "automatic-search";
  error.value = null;
  message.value = null;
  try {
    if (scope.value === "system") {
      await saveServerAutomaticSearch(enabled, provider?.provider_id ?? null);
    } else {
      const result = await queuePreferences(
        provider
          ? {
              metadata_automatic_search_providers: {
                ...preferences.value.metadata_automatic_search_providers,
                [provider.provider_id]: enabled,
              },
            }
          : { metadata_automatic_search_enabled: enabled },
      );
      if (currentUser.value?.id !== accountId) return;
      if (result.latest) preferences.value = result.prefs;
    }
    await refresh();
    message.value = "Saved";
  } catch (failure) {
    if (currentUser.value?.id !== accountId) return;
    error.value =
      failure instanceof Error
        ? failure.message
        : "Automatic search settings could not be saved.";
  } finally {
    busy.value = null;
  }
}
watch(scope, () => {
  for (const provider of providers.value)
    values.value[provider.provider_id] = {};
  message.value = null;
  error.value = null;
});
const shown = computed(() =>
  providers.value.filter(
    (provider) => !props.pluginId || provider.plugin_id === props.pluginId,
  ),
);
function fields(provider: MetadataProviderStatus) {
  return provider.configuration.filter(
    (field) => field.scope === scope.value || field.scope === "both",
  );
}
function visual(provider: MetadataProviderStatus) {
  return (
    SOURCE_VISUALS[provider.name] ?? {
      bg: "var(--ui-bg-soft)",
      fg: "var(--ui-accent-text)",
      mark: provider.name.replace(/[^a-zA-Z]/g, "").slice(0, 2),
    }
  );
}
function statusClass(provider: MetadataProviderStatus) {
  if (!accountEnabled(provider)) return "disconnected";
  if (provider.state === "healthy") return "connected";
  if (
    [
      "rate_limited",
      "temporarily_unavailable",
      "invalid_configuration",
    ].includes(provider.state)
  )
    return "error";
  return "disconnected";
}
function description(provider: MetadataProviderStatus) {
  const media = {
    game: "Games",
    movie: "Movies",
    tv_show: "TV",
    anime: "Anime",
  };
  return `${provider.media_types.map((type) => media[type]).join(", ")} · ${[
    provider.operations.metadata ? "metadata" : null,
    provider.operations.media ? "artwork" : null,
  ]
    .filter(Boolean)
    .join(" & ")}`;
}
async function refresh() {
  try {
    providers.value = await fetchMetadataProviders();
    serverAutomaticSearch.value = await fetchServerAutomaticSearch();
    for (const provider of providers.value)
      values.value[provider.provider_id] ??= {};
  } catch (failure) {
    error.value =
      failure instanceof Error
        ? failure.message
        : "Provider status is unavailable.";
  } finally {
    loading.value = false;
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
    message.value = accountEnabled(provider)
      ? "Saved. Validation runs in the background; refresh status to see its result."
      : "Saved. This provider remains disabled for your account.";
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
    <div class="provider-toolbar">
      <h3 class="group-heading">Metadata</h3>
      <router-link v-if="!pluginId" to="/settings?section=plugins"
        >Optional plugins</router-link
      >
      <button
        type="button"
        class="secondary-button"
        :disabled="busy !== null"
        @click="refresh"
      >
        Refresh status
      </button>
    </div>
    <label v-if="currentUser?.is_admin" class="field scope-field">
      <span>Provider settings for</span>
      <select v-model="scope" :disabled="busy !== null">
        <option value="user">My account</option>
        <option value="system">System default</option>
      </select>
    </label>
    <p v-if="scope === 'user'" class="section-hint">
      Choose the providers this account uses for searches, metadata and artwork,
      including providers with server credentials. These choices do not change
      connected-library ownership, playtime or achievement sync.
    </p>
    <p class="section-hint">
      Core providers are included with the app. Steam game search, TVmaze and
      anime search work without keys; additional providers are optional. Your
      account credentials take precedence over system defaults. Saved secrets
      stay hidden.
    </p>
    <label v-if="!pluginId" class="automatic-search-setting">
      <input
        type="checkbox"
        :checked="masterAutomaticSearch"
        :disabled="busy !== null || !preferencesLoaded"
        @change="
          changeAutomaticSearch(($event.target as HTMLInputElement).checked)
        "
      />
      <span>
        <strong
          >Automatic Search{{
            scope === "system" ? " · server defaults" : ""
          }}</strong
        >
        <small class="section-hint">
          Search metadata providers while typing when adding games or media, or
          looking up metadata. Search buttons, Enter, selecting a result and
          library filtering continue to work.
          <template v-if="scope === 'system'">
            Server limits also cover providers without credentials. Users with
            their own complete credentials can override these limits.
          </template>
        </small>
      </span>
    </label>
    <p v-if="loading" class="section-hint" role="status">Loading providers…</p>
    <p v-else-if="!shown.length" class="section-hint">
      Core providers are unavailable. Refresh status or check the server logs.
    </p>
    <div class="source-grid">
      <article
        v-for="provider in shown"
        :key="provider.provider_id"
        class="source-tile"
      >
        <div
          class="tile-icon"
          :style="{
            background: visual(provider).bg,
            color: visual(provider).fg,
          }"
        >
          {{ visual(provider).mark }}
        </div>
        <div class="tile-body">
          <span class="tile-name" :title="provider.name">{{
            provider.name
          }}</span>
          <span
            class="tile-status"
            :class="statusClass(provider)"
            role="status"
            >{{
              !accountEnabled(provider)
                ? "Disabled for your account"
                : (states[provider.state] ?? provider.state)
            }}</span
          >
        </div>
        <p class="tile-desc" :title="description(provider)">
          {{ description(provider) }}
          <span v-if="provider.included" class="tile-hint"> · Built in</span>
        </p>
        <label
          v-if="scope === 'user'"
          class="automatic-search-setting provider-choice"
        >
          <input
            type="checkbox"
            :aria-label="`Use ${provider.name} for metadata`"
            :checked="accountEnabled(provider)"
            :disabled="busy !== null || !preferencesLoaded"
            @change="
              changeProviderEnabled(provider, $event.target as HTMLInputElement)
            "
          />
          <span>Use for metadata</span>
        </label>
        <div class="tile-actions">
          <button
            type="button"
            class="icon-btn"
            :class="{ active: expanded[provider.provider_id] }"
            :aria-label="`Configure ${provider.name}`"
            :aria-expanded="!!expanded[provider.provider_id]"
            @click="
              expanded[provider.provider_id] = !expanded[provider.provider_id]
            "
          >
            <svg
              viewBox="0 0 24 24"
              width="14"
              height="14"
              fill="none"
              stroke="currentColor"
              stroke-width="2"
              stroke-linecap="round"
              stroke-linejoin="round"
            >
              <path
                d="M21 2l-2 2m-7.61 7.61a5.5 5.5 0 1 1-7.778 7.778 5.5 5.5 0 0 1 7.777-7.777zm0 0L15.5 7.5m0 0l3 3L22 7l-3-3m-3.5 3.5L19 4"
              />
            </svg>
          </button>
          <span class="tile-hint">{{
            fields(provider).length
              ? "Credentials"
              : provider.configuration.length
                ? "System credentials"
                : "No key needed"
          }}</span>
        </div>
        <form
          v-if="expanded[provider.provider_id]"
          class="tile-form"
          @submit.prevent="save(provider)"
        >
          <p v-if="provider.checked_at" class="tile-hint">
            Last checked
            {{ new Date(provider.checked_at * 1000).toLocaleString() }}
          </p>
          <label class="automatic-search-setting">
            <input
              type="checkbox"
              :checked="providerAutomaticSearch(provider)"
              :disabled="
                busy !== null ||
                !masterAutomaticSearch ||
                !preferencesLoaded ||
                (scope === 'user' && !accountEnabled(provider))
              "
              @change="
                changeAutomaticSearch(
                  ($event.target as HTMLInputElement).checked,
                  provider,
                )
              "
            />
            <span>Automatic Search</span>
          </label>
          <p v-if="!accountEnabled(provider)" class="tile-hint">
            Disabled for your account. Searches, metadata updates and artwork
            from this provider are off, including when server credentials are
            available. Your credentials and Automatic Search choice are kept.
            Connected-library ownership, playtime and achievement sync are
            unaffected.
          </p>
          <p v-if="!masterAutomaticSearch" class="tile-hint">
            The
            {{ scope === "system" ? "server default" : "account" }} Automatic
            Search switch is off.
          </p>
          <p
            v-else-if="
              scope === 'user' &&
              provider.automatic_search?.reason === 'server_disabled'
            "
            class="tile-hint"
          >
            Automatic use of this server provider is disabled by an
            administrator. Complete personal credentials take precedence where
            this provider supports them.
          </p>
          <p
            v-if="provider.configuration.length && !fields(provider).length"
            class="tile-hint"
          >
            An administrator configures this provider's system credentials.
          </p>
          <p
            v-if="
              (provider.state === 'disabled' && accountEnabled(provider)) ||
              provider.state === 'plugin_unavailable'
            "
            class="tile-hint"
          >
            Enable this provider through Plugin Manager.
          </p>
          <label
            v-for="field in fields(provider)"
            :key="field.key"
            class="field"
          >
            <span
              >{{ field.label
              }}<span v-if="field.required"> (required)</span></span
            >
            <small class="tile-hint">{{
              provider.configured_fields[field.key]?.[scope]
                ? "Saved in this scope"
                : "No saved value in this scope"
            }}</small>
            <PasswordInput
              v-if="field.secret"
              :model-value="values[provider.provider_id]?.[field.key] ?? ''"
              autocomplete="new-password"
              placeholder="Leave blank to keep"
              @update:model-value="
                values[provider.provider_id]![field.key] = $event
              "
            />
            <input
              v-else
              v-model="values[provider.provider_id]![field.key]"
              type="text"
              autocomplete="off"
              placeholder="Leave blank to keep"
            />
          </label>
          <div v-if="fields(provider).length" class="card-actions">
            <button
              type="submit"
              class="primary-button"
              :disabled="busy !== null"
            >
              Save
            </button>
            <button
              type="button"
              class="secondary-button"
              :disabled="busy !== null"
              @click="save(provider, true)"
            >
              Clear this scope
            </button>
          </div>
        </form>
      </article>
    </div>
    <p v-if="message" class="form-success" role="status">{{ message }}</p>
    <p v-if="error" class="form-error" role="alert">{{ error }}</p>
  </section>
</template>

<style scoped src="../../styles/pages/metadata-sources.css" />
<style scoped>
.provider-toolbar {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  flex-wrap: wrap;
}
.provider-toolbar .group-heading {
  margin: 0;
  margin-right: auto;
}
.provider-toolbar a {
  color: var(--ui-accent-text);
  font-size: 0.75rem;
}
.scope-field {
  max-width: 16rem;
  margin-block: 0.75rem;
}
.automatic-search-setting {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  margin-block: 12px;
}
.automatic-search-setting small {
  display: block;
  margin-top: 4px;
}
.automatic-search-setting input {
  accent-color: var(--ui-accent);
}
.provider-choice {
  min-height: 44px;
  align-items: center;
  margin-block: 0;
}
</style>
