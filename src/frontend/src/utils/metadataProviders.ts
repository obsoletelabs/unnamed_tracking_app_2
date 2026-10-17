import type {
  MetadataMediaType,
  MetadataProviderStatus,
} from "../services/metadata";

// Shared by the provider tiles and the scan order so both describe a state alike.
export const PROVIDER_STATE_LABELS: Record<string, string> = {
  disabled: "Disabled",
  not_configured: "Not configured",
  unvalidated: "Awaiting validation",
  healthy: "Healthy",
  temporarily_unavailable: "Temporarily unavailable",
  rate_limited: "Rate limited",
  plugin_unavailable: "Plugin unavailable",
  invalid_configuration: "Invalid configuration",
};

export const MEDIA_TYPE_LABELS: Record<MetadataMediaType, string> = {
  game: "Games",
  movie: "Movies",
  tv_show: "TV",
  anime: "Anime",
};

// The backend ranks a provider missing from a saved order after every listed one.
export const UNRANKED = 100;
const PROVIDER_ID = /^[a-z0-9][a-z0-9._-]{0,127}$/;

export type ProviderOrderKind = "metadata" | "artwork";

export const METADATA_SETTINGS_LINKS = {
  sources: "/settings?area=preferences&section=metadata&tab=sources",
  plugins: "/settings?area=administration&section=plugins",
  serverIntegrations:
    "/settings?area=administration&section=server-integrations",
} as const;

/** Search and metadata providers form one order; artwork providers form the other. */
export function participatesIn(
  provider: MetadataProviderStatus,
  kind: ProviderOrderKind,
): boolean {
  return kind === "metadata"
    ? Boolean(provider.operations.search || provider.operations.metadata)
    : Boolean(provider.operations.media);
}

function rank(provider: MetadataProviderStatus, kind: ProviderOrderKind) {
  return (
    (kind === "metadata" ? provider.priority : provider.artwork_priority) ??
    UNRANKED
  );
}

/**
 * Every registered provider in the order the server applies (rank, then ID, as
 * the backend breaks ties), followed by saved entries the server does not list
 * now, such as an uninstalled plugin, so a later reinstall keeps its place.
 */
export function effectiveProviderOrder(
  providers: MetadataProviderStatus[],
  kind: ProviderOrderKind,
  saved: string[],
): string[] {
  const ordered = providers
    .filter((provider) => participatesIn(provider, kind))
    .sort(
      (left, right) =>
        rank(left, kind) - rank(right, kind) ||
        (left.provider_id < right.provider_id ? -1 : 1),
    )
    .map((provider) => provider.provider_id);
  // Saved entries are provider IDs or capitalised legacy names ("GiantBomb"),
  // which the server matches to a provider's display name.
  const known = new Set(
    providers.flatMap((provider) =>
      PROVIDER_ID.test(provider.name)
        ? [provider.provider_id]
        : [provider.provider_id, provider.name],
    ),
  );
  return [...ordered, ...saved.filter((entry) => !known.has(entry))];
}

/**
 * Swap a provider with its neighbour in the visible media-type list.
 *
 * Each provider has one position shared by all its media types. Hidden entries
 * between the two may cover another media type, so either the clicked provider
 * or its neighbour can move; the one that changes fewer other lists moves.
 */
export function moveProvider(
  order: string[],
  visible: string[],
  providerId: string,
  direction: -1 | 1,
  mediaTypes: Map<string, readonly string[]>,
): string[] | null {
  const position = visible.indexOf(providerId);
  const neighbour = visible[position + direction];
  if (position < 0 || neighbour === undefined) return null;
  const [upper, lower] =
    direction === -1 ? [neighbour, providerId] : [providerId, neighbour];
  const upperIndex = order.indexOf(upper);
  const lowerIndex = order.indexOf(lower);
  if (upperIndex < 0 || lowerIndex < 0) return null;
  const between = order.slice(upperIndex + 1, lowerIndex);
  const conflicts = (id: string) => {
    const types = mediaTypes.get(id) ?? [];
    return between.filter((other) =>
      (mediaTypes.get(other) ?? []).some((type) => types.includes(type)),
    ).length;
  };
  const moving =
    conflicts(upper) === conflicts(lower)
      ? providerId
      : conflicts(upper) < conflicts(lower)
        ? upper
        : lower;
  const next = order.filter((id) => id !== moving);
  if (moving === upper) next.splice(next.indexOf(lower) + 1, 0, upper);
  else next.splice(next.indexOf(upper), 0, lower);
  return next;
}

export interface ProviderAvailability {
  tone: "ready" | "warning" | "unavailable";
  label: string;
  hint: string | null;
  action: { label: string; to: string } | null;
}

/**
 * Explain whether the server can use a provider for this account. Administrators
 * get a link to the setting that fixes it; other accounts learn who can.
 */
export function providerAvailability(
  provider: MetadataProviderStatus,
  isAdmin: boolean,
): ProviderAvailability {
  const personal = provider.configuration.some(
    (field) => field.scope !== "system",
  );
  const credentials = (
    problem: "Needs" | "Invalid",
    fixed: "added" | "fixed",
  ): ProviderAvailability => ({
    tone: "unavailable",
    label: `${problem} ${personal ? "" : "server "}credentials`,
    hint:
      personal || isAdmin
        ? `Skipped until ${fixed}`
        : `Skipped until an administrator ${fixed === "added" ? "adds" : "fixes"} them`,
    action: personal
      ? { label: "Add credentials", to: METADATA_SETTINGS_LINKS.sources }
      : isAdmin
        ? {
            label: "Server integrations",
            to: METADATA_SETTINGS_LINKS.serverIntegrations,
          }
        : null,
  });
  const plugin = (label: string, admin: string, member: string) => ({
    tone: "unavailable" as const,
    label,
    hint: isAdmin ? admin : member,
    action: isAdmin
      ? { label: "Plugin Manager", to: METADATA_SETTINGS_LINKS.plugins }
      : null,
  });
  switch (provider.state) {
    case "healthy":
      return { tone: "ready", label: "Ready", hint: null, action: null };
    case "unvalidated":
      return {
        tone: "ready",
        label: PROVIDER_STATE_LABELS.unvalidated,
        hint: null,
        action: null,
      };
    case "not_configured":
      return credentials("Needs", "added");
    case "invalid_configuration":
      return credentials("Invalid", "fixed");
    case "disabled":
      return plugin(
        "Disabled on this server",
        "Skipped until its plugin is enabled",
        "Skipped until an administrator enables its plugin",
      );
    case "plugin_unavailable":
      return plugin(
        PROVIDER_STATE_LABELS.plugin_unavailable,
        "Skipped until its plugin is running",
        "Skipped until an administrator restarts its plugin",
      );
    default:
      // Still tried by searches; the label says why results may be missing.
      return {
        tone: "warning",
        label: PROVIDER_STATE_LABELS[provider.state] ?? provider.state,
        hint: null,
        action: null,
      };
  }
}

/** Provider use is keyed by provider ID; rows saved earlier used display names. */
export function lastUsedLabel(
  provider: MetadataProviderStatus,
  lastUsed: Record<string, number>,
  now = Date.now() / 1000,
): string {
  const at = lastUsed[provider.provider_id] ?? lastUsed[provider.name];
  if (!at) return "Not used yet";
  const seconds = now - at;
  if (seconds < 60) return "Used just now";
  if (seconds < 3600) return `Used ${Math.round(seconds / 60)}m ago`;
  if (seconds < 86400) return `Used ${Math.round(seconds / 3600)}h ago`;
  return `Used ${Math.round(seconds / 86400)}d ago`;
}
