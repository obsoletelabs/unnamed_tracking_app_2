// Provider availability is derived from server state that Settings and the
// Plugin Manager change. They announce each change here so forms that explain
// availability (#254) refresh without being reopened.
export const METADATA_PROVIDERS_CHANGED = "metadata-providers-changed";

export function announceMetadataProvidersChanged(): void {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new Event(METADATA_PROVIDERS_CHANGED));
}
