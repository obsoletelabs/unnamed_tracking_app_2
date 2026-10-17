import type {
  NotificationRoutingDestination,
  NotificationRoutingProvider,
} from "../services/notifications";

export type ProviderListing = "active" | "disabled" | "unavailable" | "hidden";

/**
 * How a provider appears in the settings view.
 * - active: can be added or is working; full panel (collapsed by default)
 * - disabled: supported and configured but switched off for now; compact row
 * - unavailable: not installed/granted; folded into the "unavailable" list
 *   only when saved destinations exist, otherwise hidden entirely
 */
export function providerListing(
  provider: NotificationRoutingProvider,
  destinations: NotificationRoutingDestination[],
): ProviderListing {
  const mine = destinations.filter((d) => d.provider_id === provider.id);
  if (!provider.available && !provider.configuration_available)
    return mine.length ? "unavailable" : "hidden";
  if (provider.configuration_scope === "internal") return "active";
  if (!provider.enabled && mine.some((d) => d.active)) return "disabled";
  return "active";
}

/** A destination may be offered as a per-type routing choice right now. */
export function isRoutable(d: NotificationRoutingDestination): boolean {
  return d.active && d.available && d.enabled && d.provider_enabled;
}

/** One-line status for a provider that is configured but switched off. */
export function disabledSummary(
  provider: NotificationRoutingProvider,
  destinations: NotificationRoutingDestination[],
): string {
  const count = destinations.filter(
    (d) => d.provider_id === provider.id && d.active,
  ).length;
  return `Temporarily disabled · ${count} destination${count === 1 ? "" : "s"} and routing choices kept`;
}
