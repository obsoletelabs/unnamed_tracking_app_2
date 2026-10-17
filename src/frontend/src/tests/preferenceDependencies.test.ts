import { expect, it } from "vitest";
import {
  calendarGameUnavailable,
  notificationTypeUnavailable,
  routeUnavailable,
  unavailableText,
} from "../services/preferenceDependencies";
import { DEFAULT_PREFERENCES } from "../services/preferences";

const destination = {
  active: true,
  available: true,
  enabled: true,
  provider_enabled: true,
  provider_name: "Discord",
  context: "external" as const,
  eligible_types: ["media.franchise.season"],
};

it("explains franchise types while anime is not followed and restores on re-enable", () => {
  const stored = {
    ...DEFAULT_PREFERENCES,
    notification_types: { "media.franchise.season": true },
    notify_media_types: ["tv", "movie"] as ("anime" | "tv" | "movie")[],
  };
  const snapshot = JSON.stringify(stored);
  const blocked = notificationTypeUnavailable("media.franchise.season", stored);
  expect(blocked?.reason).toContain("Anime");
  expect(unavailableText(blocked)).toContain("Your choice is kept.");
  expect(JSON.stringify(stored)).toBe(snapshot);
  const restored = {
    ...stored,
    notify_media_types: ["anime", "tv"] as typeof stored.notify_media_types,
  };
  expect(
    notificationTypeUnavailable("media.franchise.season", restored),
  ).toBeNull();
  expect(restored.notification_types["media.franchise.season"]).toBe(true);
});

it("requires a watch status only for status-filtered types", () => {
  const prefs = { ...DEFAULT_PREFERENCES, notify_statuses: [] };
  expect(
    notificationTypeUnavailable("media.episode.aired", prefs)?.reason,
  ).toContain("status");
  expect(
    notificationTypeUnavailable("media.franchise.extra", prefs),
  ).toBeNull();
  expect(
    notificationTypeUnavailable("security.session.anomaly", prefs),
  ).toBeNull();
});

it("orders route reasons by authority: policy, parent type, provider, account", () => {
  const type = "media.franchise.season";
  expect(
    routeUnavailable(
      "New seasons",
      true,
      { ...destination, eligible_types: [] },
      type,
      true,
    )?.reason,
  ).toContain("trust");
  expect(
    routeUnavailable("New seasons", false, destination, type, true)?.enable,
  ).toContain("Turn on New seasons");
  expect(
    routeUnavailable(
      "New seasons",
      true,
      { ...destination, provider_enabled: false },
      type,
      true,
    )?.enable,
  ).toContain("Your providers");
  expect(
    routeUnavailable("New seasons", true, destination, type, false)?.reason,
  ).toContain("turned off");
  expect(
    routeUnavailable(
      "New seasons",
      true,
      { ...destination, active: false },
      type,
      true,
    )?.reason,
  ).toContain("inactive");
  expect(
    routeUnavailable("New seasons", true, destination, type, true),
  ).toBeNull();
});

it("disables calendar game layers only while all games are hidden", () => {
  expect(
    calendarGameUnavailable({ calendar_hide_games: true })?.enable,
  ).toContain("Hide all Games layers");
  expect(calendarGameUnavailable({ calendar_hide_games: false })).toBeNull();
});

it("explains movie releases when only the Watching status is followed", () => {
  const only = {
    ...DEFAULT_PREFERENCES,
    notify_statuses: ["watching" as const],
  };
  expect(
    notificationTypeUnavailable("media.movie.released", only)?.enable,
  ).toContain("Plan to Watch");
  expect(notificationTypeUnavailable("media.episode.aired", only)).toBeNull();
  const planned = { ...only, notify_statuses: ["plan" as const] };
  expect(
    notificationTypeUnavailable("media.movie.released", planned),
  ).toBeNull();
  const none = { ...only, notify_statuses: [] };
  expect(
    notificationTypeUnavailable("media.movie.released", none)?.reason,
  ).toContain("no watch status");
});
