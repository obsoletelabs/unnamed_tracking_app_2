// Kept apart from the metadata service so that anything announcing a change
// (a saved key, a connection, a provider switch) can do so without depending on it.
export const PROVIDER_CHANGE_EVENT = "metadata-provider-configuration-changed";

/**
 * Tell forms that explain a missing setup to re-check which sources can run.
 * Called after anything that saves a key, a connection or a provider switch.
 */
export function announceProviderChange(): void {
  if (typeof window !== "undefined")
    window.dispatchEvent(new Event(PROVIDER_CHANGE_EVENT));
}
