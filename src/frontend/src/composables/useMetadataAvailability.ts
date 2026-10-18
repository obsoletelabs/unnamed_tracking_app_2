import { computed, onMounted, onScopeDispose, ref, toValue } from "vue";
import type { MaybeRefOrGetter } from "vue";
import { currentUser } from "../state/auth";
import { preferences } from "../state/preferences";
import { fetchMetadataProviders } from "../services/metadata";
import type {
  MetadataMediaType,
  MetadataProviderStatus,
} from "../services/metadata";
import {
  searchAvailability,
  type SearchAvailability,
} from "../utils/metadataSearchAvailability";
import { METADATA_PROVIDERS_CHANGED } from "../utils/metadataProviderEvents";

/**
 * Whether metadata searches for these media types can reach a provider, kept
 * current when providers are configured, enabled, disabled or removed (#254).
 * A refresh never touches anything the caller holds, such as unsaved form
 * values. Until the provider list loads (or if it cannot load) searching stays
 * allowed and the server remains the authority.
 */
export function useMetadataAvailability(
  mediaTypes: MaybeRefOrGetter<MetadataMediaType[]>,
) {
  const providers = ref<MetadataProviderStatus[]>([]);
  const loaded = ref(false);
  let request = 0;

  async function refresh() {
    const current = ++request;
    try {
      const found = await fetchMetadataProviders();
      if (current !== request) return;
      providers.value = found;
      loaded.value = true;
    } catch {
      // keep the last known list; the server still checks every search
    }
  }

  const availability = computed<SearchAvailability>(() =>
    loaded.value
      ? searchAvailability(
          providers.value,
          toValue(mediaTypes),
          currentUser.value?.is_admin ?? false,
          (provider) =>
            preferences.value.metadata_providers_enabled?.[
              provider.provider_id
            ] ??
            provider.account_enabled ??
            true,
        )
      : { available: true, reason: null, action: null },
  );

  function onVisible() {
    if (!document.hidden) void refresh();
  }
  onMounted(() => {
    void refresh();
    window.addEventListener(METADATA_PROVIDERS_CHANGED, refresh);
    window.addEventListener("focus", refresh);
    document.addEventListener("visibilitychange", onVisible);
  });
  onScopeDispose(() => {
    window.removeEventListener(METADATA_PROVIDERS_CHANGED, refresh);
    window.removeEventListener("focus", refresh);
    document.removeEventListener("visibilitychange", onVisible);
  });

  return { providers, loaded, availability, refresh };
}
