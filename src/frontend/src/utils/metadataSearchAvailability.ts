import type {
  MetadataMediaType,
  MetadataProviderStatus,
} from "../services/metadata";
import {
  MEDIA_TYPE_LABELS,
  METADATA_SETTINGS_LINKS,
  providerAvailability,
} from "./metadataProviders";

export interface SearchAvailability {
  available: boolean;
  reason: string | null;
  action: { label: string; to: string } | null;
}

const UNUSABLE_STATES = [
  "disabled",
  "not_configured",
  "invalid_configuration",
  "plugin_unavailable",
];

/**
 * Whether a metadata search for these media types can reach any provider, and
 * if not, which setup is missing. Only labels and states are used, never
 * credential values. The server decides again when the search starts.
 */
export function searchAvailability(
  providers: MetadataProviderStatus[],
  mediaTypes: MetadataMediaType[],
  isAdmin: boolean,
  accountEnabled: (provider: MetadataProviderStatus) => boolean = (provider) =>
    provider.account_enabled ?? true,
): SearchAvailability {
  const covering = providers.filter(
    (provider) =>
      provider.operations.search &&
      provider.media_types.some((type) => mediaTypes.includes(type)),
  );
  if (
    covering.some(
      (provider) =>
        accountEnabled(provider) && !UNUSABLE_STATES.includes(provider.state),
    )
  )
    return { available: true, reason: null, action: null };
  const labels = mediaTypes.map((type) => MEDIA_TYPE_LABELS[type]).join(", ");
  if (!covering.length)
    return {
      available: false,
      reason: `No metadata provider is installed for ${labels}.`,
      action: isAdmin
        ? { label: "Plugin Manager", to: METADATA_SETTINGS_LINKS.plugins }
        : null,
    };
  const turnedOff = covering.filter((provider) => !accountEnabled(provider));
  const broken = covering.filter((provider) => accountEnabled(provider));
  if (!broken.length)
    return {
      available: false,
      reason: `${turnedOff.map((provider) => provider.name).join(", ")} ${
        turnedOff.length === 1 ? "is" : "are"
      } turned off for your account.`,
      action: {
        label: "Metadata sources",
        to: METADATA_SETTINGS_LINKS.sources,
      },
    };
  const details = broken.slice(0, 3).map((provider) => {
    const found = providerAvailability(provider, isAdmin);
    return `${provider.name}: ${found.label.toLowerCase()}${found.hint ? `. ${found.hint}` : ""}`;
  });
  return {
    available: false,
    reason: `No metadata provider is ready for ${labels}. ${details.join(". ")}.`,
    action: providerAvailability(broken[0], isAdmin).action,
  };
}
