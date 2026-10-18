// Whether a metadata search can work for a kind of media, and if not, why and
// where to fix it (#254). This only explains: the server still checks the same
// things when a search starts, because configuration and permissions can change
// after a page was drawn. It never exposes a credential, only which setting is
// missing.
import type { MetadataMediaType, MetadataProviderStatus } from "./metadata";

export type BlockKind = "none" | "disabled" | "not_configured" | "unavailable";

export interface SetupLink {
  label: string;
  href: string;
}

export interface SearchBlock {
  kind: BlockKind;
  message: string;
  setup: SetupLink | null;
}

export const MEDIA_NAMES: Record<MetadataMediaType, string> = {
  game: "games",
  movie: "movies",
  tv_show: "TV shows",
  anime: "anime",
};

export const SOURCES_LINK: SetupLink = {
  label: "Settings › Metadata › Sources & API keys",
  href: "/settings?section=metadata&tab=sources",
};
export const PROVIDERS_LINK: SetupLink = {
  label: "Settings › Metadata › Scan & providers",
  href: "/settings?section=metadata&tab=scan",
};
export const PLUGINS_LINK: SetupLink = {
  label: "Settings › Administration › Plugins",
  href: "/settings?area=administration&section=plugins",
};

function missingFields(provider: MetadataProviderStatus): string[] {
  return provider.configuration
    .filter(
      (field) =>
        field.required &&
        !provider.configured_fields[field.key]?.system &&
        !provider.configured_fields[field.key]?.user,
    )
    .map((field) => field.label);
}

/** Null when at least one source can be searched for this kind of media. */
export function searchBlock(
  providers: readonly MetadataProviderStatus[],
  mediaType: MetadataMediaType,
  isAdmin = false,
): SearchBlock | null {
  const name = MEDIA_NAMES[mediaType];
  const covering = providers.filter(
    (provider) =>
      provider.media_types.includes(mediaType) && provider.operations.search,
  );
  if (!covering.length)
    return {
      kind: "none",
      message: `No metadata source supports searching for ${name}.`,
      setup: isAdmin ? PLUGINS_LINK : null,
    };
  const enabled = covering.filter(
    (provider) =>
      provider.account_enabled !== false && provider.state !== "disabled",
  );
  if (!enabled.length)
    return {
      kind: "disabled",
      message: `Every source for ${name} is turned off for your account.`,
      setup: PROVIDERS_LINK,
    };
  const usable = enabled.filter(
    (provider) =>
      provider.state !== "not_configured" &&
      provider.state !== "plugin_unavailable",
  );
  if (usable.length) return null;
  const waiting = enabled.filter((p) => p.state === "not_configured");
  if (waiting.length) {
    const needs = waiting
      .map((provider) => {
        const fields = missingFields(provider);
        return fields.length
          ? `${provider.name} needs ${fields.join(" and ")}`
          : `${provider.name} is not set up`;
      })
      .join("; ");
    return {
      kind: "not_configured",
      message: `${needs}.`,
      setup: SOURCES_LINK,
    };
  }
  return {
    kind: "unavailable",
    message: `The sources for ${name} are not running right now.`,
    setup: isAdmin ? PLUGINS_LINK : null,
  };
}
