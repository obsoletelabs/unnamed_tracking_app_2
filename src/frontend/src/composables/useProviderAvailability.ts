import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { currentUser } from "../state/auth";
import { fetchMetadataProviders } from "../services/metadata";
import { PROVIDER_CHANGE_EVENT } from "../services/providerEvents";
import type {
  MetadataMediaType,
  MetadataProviderStatus,
} from "../services/metadata";
import { searchBlock } from "../services/providerAvailability";

// One shared copy of the provider list: opening several forms does not ask
// again, and saving a key (or switching a provider) elsewhere refreshes every
// open form. It is also re-read when the window regains focus, so setting a
// key up in another tab clears the notice without losing a half-filled form.
const providers = ref<MetadataProviderStatus[] | null>(null);
const failed = ref(false);
let loadedAt = 0;
let inflight: Promise<void> | null = null;
const FRESH_MS = 20_000;

export function refreshProviderAvailability(force = false): Promise<void> {
  if (!force && providers.value && Date.now() - loadedAt < FRESH_MS)
    return Promise.resolve();
  // a synchronous failure (a missing export in a test double, say) is a failed check too
  inflight ??= Promise.resolve()
    .then(() => fetchMetadataProviders())
    .then((list) => {
      providers.value = list;
      failed.value = false;
      loadedAt = Date.now();
    })
    .catch(() => {
      // keep what was known; the server check on submit is still authoritative
      failed.value = true;
    })
    .finally(() => {
      inflight = null;
    });
  return inflight;
}

// Saving a key, a connection or a provider switch anywhere refreshes every open form.
if (typeof window !== "undefined")
  window.addEventListener(PROVIDER_CHANGE_EVENT, () => {
    void refreshProviderAvailability(true);
  });

export function useProviderAvailability(mediaType: MetadataMediaType) {
  const block = computed(() =>
    providers.value
      ? searchBlock(
          providers.value,
          mediaType,
          currentUser.value?.is_admin ?? false,
        )
      : null,
  );
  const onFocus = () => void refreshProviderAvailability();
  onMounted(() => {
    void refreshProviderAvailability();
    window.addEventListener("focus", onFocus);
  });
  onBeforeUnmount(() => window.removeEventListener("focus", onFocus));
  return {
    block,
    known: computed(() => providers.value !== null),
    failed,
    refresh: () => refreshProviderAvailability(true),
  };
}

/** For tests. */
export function resetProviderAvailability(): void {
  providers.value = null;
  failed.value = false;
  loadedAt = 0;
  inflight = null;
}
