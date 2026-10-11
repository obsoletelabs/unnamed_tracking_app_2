// Media notifications from the server: episode aired, season started
// airing, new season listed for something you finished, movie released.
// Exact release discovery also runs in the server's scheduled jobs loop.

import { failedRequest } from "./apiError";

export type MediaNotificationKind =
  "episode_aired" | "season_started" | "sequel_announced" | "movie_released";

export interface MediaNotification {
  id: string;
  kind: string;
  mediaType: string;
  mediaId: string;
  title: string;
  body: string;
  posterUrl: string | null;
  eventAt: number;
  read: boolean;
  eventType?: string;
  source?: string;
  severity?: "info" | "warning" | "error";
  purpose?: string;
  requiredTrust?: number;
  groupKey?: string | null;
  createdAt?: number;
  readAt?: number | null;
}

interface BackendNotification {
  id: string;
  kind: string;
  media_type: string;
  media_id: string;
  title: string;
  body: string;
  poster_url: string | null;
  event_at: number;
  read: boolean;
  event_type?: string;
  source?: string;
  severity?: "info" | "warning" | "error";
  purpose?: string;
  required_trust?: number;
  group_key?: string | null;
  created_at?: number;
  read_at?: number | null;
}

export type InboxFilter =
  | "all"
  | "unread"
  | "episodes"
  | "seasons"
  | "releases"
  | "security"
  | "plugins";
export interface InboxOptions {
  category?: InboxFilter;
  search?: string;
  source?: string;
  severity?: "" | "info" | "warning" | "error";
  offset?: number;
}
export interface InboxPage {
  items: MediaNotification[];
  unread: number;
  total: number;
  counts: Partial<Record<InboxFilter, number>>;
  nextOffset: number | null;
  sources: string[];
}
export interface InboxRetentionPolicy {
  default_days: number;
  maximum_days: number;
  requested_days: number;
  effective_days: number;
  inherited: boolean;
  limited: boolean;
}

async function ok(response: Response, action: string): Promise<Response> {
  if (!response.ok) throw new Error(`Failed to ${action}: ${response.status}`);
  return response;
}

export async function fetchMediaNotifications(
  limit = 50,
  options: InboxOptions = {},
): Promise<InboxPage> {
  const parameters = new URLSearchParams({ limit: String(limit) });
  for (const [name, value] of Object.entries(options)) {
    if (value !== undefined && value !== "")
      parameters.set(name, String(value));
  }
  const response = await ok(
    await fetch(`/api/notifications?${parameters}`, {
      credentials: "include",
    }),
    "load notifications",
  );
  const raw = (await response.json()) as {
    items: BackendNotification[];
    unread: number;
    total?: number;
    counts?: Partial<Record<InboxFilter, number>>;
    next_offset?: number | null;
    sources?: string[];
  };
  return {
    unread: raw.unread,
    total: raw.total ?? raw.items.length,
    counts: raw.counts ?? {},
    nextOffset: raw.next_offset ?? null,
    sources: raw.sources ?? [],
    items: raw.items.map((n) => ({
      id: n.id,
      kind: n.kind,
      mediaType: n.media_type,
      mediaId: n.media_id,
      title: n.title,
      body: n.body,
      posterUrl: n.poster_url,
      eventAt: n.event_at,
      read: n.read,
      eventType: n.event_type ?? n.kind,
      source: n.source ?? "host",
      severity: n.severity ?? "info",
      purpose: n.purpose ?? "standard",
      requiredTrust: n.required_trust ?? 1,
      groupKey: n.group_key ?? null,
      createdAt: n.created_at ?? n.event_at,
      readAt: n.read_at ?? null,
    })),
  };
}

export async function fetchInboxRetentionPolicy(): Promise<InboxRetentionPolicy> {
  const response = await ok(
    await fetch("/api/notifications/policy", { credentials: "include" }),
    "load inbox policy",
  );
  return await response.json();
}

export async function sendSmtpTest(address: string): Promise<void> {
  const response = await fetch(
    "/api/settings/notification-providers/smtp/test",
    {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ address }),
    },
  );
  if (!response.ok) throw await failedRequest(response);
  const result = (await response.json()) as {
    sent: boolean;
    error: string | null;
  };
  if (!result.sent)
    throw new Error(
      `SMTP test failed (${result.error ?? "delivery_failed"}). Check the saved server settings.`,
    );
}

export async function sendDestinationTest(
  destinationId: string,
): Promise<{ status: string }> {
  const response = await fetch(
    `/api/settings/notification-providers/destinations/${encodeURIComponent(destinationId)}/test`,
    {
      method: "POST",
      credentials: "include",
    },
  );
  if (!response.ok) throw await failedRequest(response);
  return await response.json();
}

export async function deleteMediaNotification(id: string): Promise<void> {
  await ok(
    await fetch(`/api/notifications/${encodeURIComponent(id)}`, {
      method: "DELETE",
      credentials: "include",
    }),
    "delete notification",
  );
}

export async function createTestNotification(payload: {
  kind: MediaNotificationKind;
  mediaType: "movie" | "tv" | "anime";
  title: string;
  body: string;
}): Promise<void> {
  if (import.meta.env.VITE_USE_MOCK_DATA === "true") {
    return;
  }

  await ok(
    await fetch("/api/notifications/test", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify({
        kind: payload.kind,
        media_type: payload.mediaType,
        title: payload.title,
        body: payload.body,
      }),
    }),
    "create a test notification",
  );
}

// Admin-only: runs the same generation the bell's poll triggers, on
// demand, so a just-edited air date doesn't need a poll cycle to show up.
export async function regenerateNotifications(): Promise<{ created: number }> {
  if (import.meta.env.VITE_USE_MOCK_DATA === "true") {
    return { created: 0 };
  }

  const response = await ok(
    await fetch("/api/notifications/regenerate", {
      method: "POST",
      credentials: "include",
    }),
    "regenerate notifications",
  );
  return await response.json();
}

export async function markNotificationRead(id: string): Promise<void> {
  await ok(
    await fetch(`/api/notifications/${id}/read`, {
      method: "POST",
      credentials: "include",
    }),
    "mark notification read",
  );
}

export async function markNotificationUnread(id: string): Promise<void> {
  await ok(
    await fetch(`/api/notifications/${id}/unread`, {
      method: "POST",
      credentials: "include",
    }),
    "mark notification unread",
  );
}

export async function markAllNotificationsRead(): Promise<void> {
  await ok(
    await fetch("/api/notifications/read-all", {
      method: "POST",
      credentials: "include",
    }),
    "mark notifications read",
  );
}

export async function dismissMediaNotification(id: string): Promise<void> {
  await ok(
    await fetch(`/api/notifications/${id}/dismiss`, {
      method: "POST",
      credentials: "include",
    }),
    "dismiss notification",
  );
}

export interface NotificationRoutingType {
  event_type: string;
  preference_key: string;
  label: string;
  description: string;
  required_trust: "PRIVATE" | "SECURE";
}

export interface NotificationRoutingProvider {
  id: string;
  name: string;
  enabled: boolean;
  available: boolean;
  configuration_scope: "internal" | "server" | "user";
  destination_kind?: "discord_webhook" | "browser_push" | null;
  definition?: {
    destinations: Array<{
      kind: string;
      label: string;
      privacy: "PUBLIC" | "PRIVATE";
      fields?: Array<{ id: string; label: string; required?: boolean; secret?: boolean }>;
    }>;
    server_fields?: Array<{ id: string; label: string; required?: boolean; secret?: boolean }>;
    configure_action: string;
    retire_action: string;
    test_action?: string | null;
    features?: { critical_supported?: boolean; critical_description?: string; multiple_destinations?: boolean };
  } | null;
  critical_supported: boolean;
  critical_description?: string | null;
  transport_warning?: string | null;
  secure_transport?: boolean;
}

export interface NotificationRoutingDestination {
  id: string;
  provider_id: string;
  provider_name: string;
  kind: string;
  context: "internal" | "external";
  trust: "PUBLIC" | "PRIVATE" | "SECURE";
  active: boolean;
  enabled: boolean;
  available: boolean;
  provider_enabled: boolean;
  critical_supported: boolean;
  eligible_types: string[];
  shared_configuration: boolean;
  critical_description?: string | null;
  label?: string | null;
  notification_url?: string | null;
  masked_address?: string | null;
  recovery_allowed?: boolean;
  revision?: number;
  verification_available?: boolean;
  media_disclosure_confirmed?: boolean;
  reactivation_available?: boolean;
}

export interface NotificationRoutingSettings {
  default_url?: string;
  types: NotificationRoutingType[];
  providers: NotificationRoutingProvider[];
  destinations: NotificationRoutingDestination[];
}

export async function setDestinationNotificationUrl(
  id: string,
  url: string,
): Promise<void> {
  const response = await fetch(
    `/api/settings/notification-providers/destinations/${encodeURIComponent(id)}/url`,
    {
      method: "PATCH",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ notification_url: url }),
    },
  );
  if (!response.ok) throw await failedRequest(response);
}


export async function createPluginDestination(
  providerId: string,
  kind: string,
): Promise<{ id: string; kind: string; existing: boolean }> {
  const response = await fetch(
    "/api/settings/notification-providers/plugin-destinations",
    {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ provider_id: providerId, kind }),
    },
  );
  if (!response.ok) throw await failedRequest(response);
  return await response.json();
}

export async function fetchNotificationRouting(): Promise<NotificationRoutingSettings> {
  const response = await fetch(
    "/api/settings/notification-providers/destinations",
    {
      credentials: "include",
    },
  );
  if (!response.ok)
    throw new Error("Failed to load notification destinations.");
  return response.json();
}

export async function setNotificationProviderEnabled(
  providerId: string,
  enabled: boolean,
): Promise<void> {
  const response = await fetch(
    `/api/settings/notification-providers/${encodeURIComponent(providerId)}`,
    {
      method: "PUT",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled }),
    },
  );
  if (!response.ok) throw new Error("Failed to update notification provider.");
}

async function destinationRequest<T>(
  kind: "email" | "webhook" | "browser",
  path: string,
  method: string,
  payload?: object,
): Promise<T> {
  const response = await fetch(
    `/api/settings/notification-providers/${kind}-destinations${path}`,
    {
      method,
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      ...(payload ? { body: JSON.stringify(payload) } : {}),
    },
  );
  if (!response.ok) throw await failedRequest(response);
  return response.json();
}

export function createEmailDestination(address: string, label: string) {
  return destinationRequest<{ id: string }>("email", "", "POST", {
    address,
    label,
  });
}
export function updateEmailDestination(
  id: string,
  changes: {
    address?: string;
    label?: string;
    enabled?: boolean;
    recovery_allowed?: boolean;
  },
) {
  return destinationRequest<{ updated: boolean }>(
    "email",
    `/${encodeURIComponent(id)}`,
    "PATCH",
    changes,
  );
}
export function removeEmailDestination(id: string) {
  return destinationRequest<{ removed: boolean }>(
    "email",
    `/${encodeURIComponent(id)}`,
    "DELETE",
  );
}
export function requestEmailVerification(id: string) {
  return destinationRequest<{ challenge_id: string; expires_in: number }>(
    "email",
    `/${encodeURIComponent(id)}/verification`,
    "POST",
  );
}
export function confirmEmailVerification(
  id: string,
  challengeId: string,
  code: string,
) {
  return destinationRequest<{ verified: boolean }>(
    "email",
    `/${encodeURIComponent(id)}/verification/confirm`,
    "POST",
    { challenge_id: challengeId, code },
  );
}
export function revokeEmailVerification(id: string) {
  return destinationRequest<{ revoked: boolean }>(
    "email",
    `/${encodeURIComponent(id)}/verification/revoke`,
    "POST",
  );
}

export function createWebhookDestination(
  providerId: string,
  url: string,
  label: string,
) {
  return destinationRequest<{ id: string }>("webhook", "", "POST", {
    provider_id: providerId,
    url,
    label,
  });
}
export function updateWebhookDestination(
  id: string,
  changes: {
    label?: string;
    enabled?: boolean;
    share_followed_media?: boolean;
  },
) {
  return destinationRequest<{ updated: boolean }>(
    "webhook",
    `/${encodeURIComponent(id)}`,
    "PATCH",
    changes,
  );
}
export function removeWebhookDestination(id: string) {
  return destinationRequest<{ removed: boolean }>(
    "webhook",
    `/${encodeURIComponent(id)}`,
    "DELETE",
  );
}

export interface BrowserPushConfiguration {
  enabled: boolean;
  session_authenticated: boolean;
  public_key: string;
  current_destination_id: string | null;
}

export async function fetchBrowserPushConfiguration(): Promise<BrowserPushConfiguration> {
  const response = await fetch(
    "/api/settings/notification-providers/browser-configuration",
    { credentials: "include", cache: "no-store" },
  );
  if (!response.ok) throw await failedRequest(response);
  return response.json();
}

export async function generateBrowserPushKey(): Promise<{
  public_key: string;
}> {
  const response = await fetch(
    "/api/settings/notification-providers/browser-configuration/key",
    { method: "POST", credentials: "include" },
  );
  if (!response.ok) throw await failedRequest(response);
  return response.json();
}

export function createBrowserDestination(
  subscription: PushSubscriptionJSON,
  publicKey: string,
  label: string,
) {
  return destinationRequest<{ id: string }>("browser", "", "POST", {
    subscription,
    public_key: publicKey,
    label,
  });
}

export function updateBrowserDestination(
  id: string,
  changes: { label?: string; enabled?: boolean },
) {
  return destinationRequest<{ updated: boolean }>(
    "browser",
    `/${encodeURIComponent(id)}`,
    "PATCH",
    changes,
  );
}

export function removeBrowserDestination(id: string) {
  return destinationRequest<{ removed: boolean }>(
    "browser",
    `/${encodeURIComponent(id)}`,
    "DELETE",
  );
}
