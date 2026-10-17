// Dependencies between saved preferences. A child preference that a parent
// setting, provider or destination makes ineffective is shown disabled with
// the reason and the way to re-enable it. The stored child value is never
// rewritten here, so it applies again as soon as the dependency is restored.
// Grants, destination proof, trust and privacy rules stay authoritative on
// the server; these helpers only explain them.
import type { Preferences } from "./preferences";
import type { NotificationRoutingDestination } from "./notifications";

export interface Unavailable {
  reason: string;
  enable: string;
}

export function unavailableText(item: Unavailable | null): string {
  return item ? `${item.reason} ${item.enable} Your choice is kept.` : "";
}

// Media types and status filters each notification type needs. Mirrors the
// scheduled checks in the backend (features/notifications.py).
const MEDIA_NEEDS: Record<string, { media: string[]; statuses: boolean }> = {
  "media.episode.aired": { media: ["anime", "tv"], statuses: true },
  "media.season.started": { media: ["anime", "tv"], statuses: true },
  "media.sequel.announced": { media: ["anime", "tv"], statuses: false },
  "media.movie.released": { media: ["movie"], statuses: true },
  "media.franchise.season": { media: ["anime"], statuses: false },
  "media.franchise.extra": { media: ["anime"], statuses: false },
};
const MEDIA_LABELS: Record<string, string> = {
  anime: "Anime",
  tv: "TV shows",
  movie: "Movies",
};

export function notificationTypeUnavailable(
  eventType: string,
  prefs: Pick<Preferences, "notify_media_types" | "notify_statuses">,
): Unavailable | null {
  const needs = MEDIA_NEEDS[eventType];
  if (!needs) return null;
  if (
    !needs.media.some((media) =>
      (prefs.notify_media_types as string[]).includes(media),
    )
  ) {
    const names = needs.media.map((media) => MEDIA_LABELS[media]).join(" or ");
    return {
      reason: `No effect while ${names} is not followed.`,
      enable: `Turn on ${names} under Followed media (open Providers on any media type).`,
    };
  }
  if (needs.statuses && prefs.notify_statuses.length === 0) {
    return {
      reason: "No effect while no watch status is selected.",
      enable: "Select Watching, Plan to Watch or On Hold under Followed media.",
    };
  }
  return null;
}

export function routeUnavailable(
  typeLabel: string,
  typeEnabled: boolean,
  destination: Pick<
    NotificationRoutingDestination,
    | "active"
    | "available"
    | "enabled"
    | "provider_enabled"
    | "provider_name"
    | "context"
    | "eligible_types"
  >,
  eventType: string,
  personalEnabled: boolean,
): Unavailable | null {
  if (!destination.active || !destination.available)
    return {
      reason: "This destination is inactive.",
      enable: "Reactivate or re-add it under Your providers.",
    };
  if (!destination.eligible_types.includes(eventType))
    return {
      reason:
        "This destination does not meet this type's trust or server policy requirements.",
      enable:
        "Use a destination with sufficient trust, or ask an administrator.",
    };
  if (!typeEnabled)
    return {
      reason: `${typeLabel} is off.`,
      enable: `Turn on ${typeLabel} above.`,
    };
  if (!destination.provider_enabled || !destination.enabled)
    return {
      reason: `${destination.provider_name} is disabled.`,
      enable: `Enable ${destination.provider_name} for your account under Your providers.`,
    };
  if (!personalEnabled)
    return {
      reason: "This destination is turned off for your account.",
      enable: "Turn on Use this destination under Your providers.",
    };
  return null;
}

export function calendarGameUnavailable(
  prefs: Pick<Preferences, "calendar_hide_games">,
): Unavailable | null {
  return prefs.calendar_hide_games
    ? {
        reason: "Hidden while Hide all Games layers is on.",
        enable: "Turn off Hide all Games layers below.",
      }
    : null;
}
