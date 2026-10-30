import { pluginRequestError } from "./apiError";

export interface AccessCapability {
  scope: string;
  description: string;
  write: boolean;
}
export interface AccessRequestSummary {
  external_account?: {
    provider: string;
    subject: string;
    username: string;
  } | null;
  id: string;
  name: string;
  origin: string;
  /** "desktop" for a first-party app such as Playnite, which has no web origin. */
  kind?: "web" | "desktop";
  instance_id: string;
  state: string;
  expires_at: number;
  maximum_grant_hours: number;
  capabilities: AccessCapability[];
}
export interface IntegrationGrant {
  external_account?: {
    provider: string;
    subject: string;
    username: string;
    playnite_extension_version?: string | null;
    playnite_version_checked_at?: number;
  } | null;
  id: string;
  name: string;
  origin: string;
  kind?: "web" | "desktop";
  enabled: boolean;
  scopes: string[];
  created_at: number;
  expires_at: number | null;
  revoked_at: number | null;
}
export interface IntegrationClient {
  id: string;
  name: string;
  origin: string;
  enabled: boolean;
  kind?: "web" | "desktop";
}
export interface IntegrationPolicy {
  enabled: boolean;
  allow_guests: boolean;
  allow_any_origin: boolean;
  allow_legacy_api_keys: boolean;
  allow_desktop_apps: boolean;
  guest_retention_hours: number;
  maximum_grant_hours: number;
  clients: IntegrationClient[];
}
export const capabilityLabels: Record<string, string> = {
  "profile.read": "Read display name and account identity",
  "games.read": "Read library, ownership, ratings and tracking",
  "achievements.read": "Read achievements and local reports",
  "history.read": "Read game field-change history",
  "lists.read": "Read collections and custom lists",
  "media.read": "Read tracked media",
  "tracking.write": "Change tracking, completion, playtime and ratings",
  "achievements.write": "Create manual achievements and local reports",
  "compatibility.write": "Save multiplayer settings for your games",
  "library.sync": "Add and update games, artwork and launcher details",
  "games.launch": "Receive requests to launch Playnite games on this computer",
  "saves.read": "List and download save backups",
  "saves.write": "Upload and rename save backups",
  "content.write": "Add screenshots, clips, soundtracks and documents",
};

const base = "/api/delegation/v1";
async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(base + path, {
    ...init,
    credentials: "include",
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!response.ok)
    throw await pluginRequestError(
      response,
      "Integration access request failed.",
    );
  return response.json() as Promise<T>;
}
export const fetchAccessRequest = (id: string) =>
  request<AccessRequestSummary>(`/requests/${encodeURIComponent(id)}`);
export const decideAccessRequest = (
  id: string,
  approve: boolean,
  scopes: string[],
  expiresInHours: number | null,
) =>
  request<{ state: string }>(`/requests/${encodeURIComponent(id)}/decision`, {
    method: "POST",
    body: JSON.stringify({ approve, scopes, expires_in_hours: expiresInHours }),
  });
export const fetchIntegrationGrants = (signal?: AbortSignal) =>
  request<IntegrationGrant[]>("/grants", { signal });
export const revokeIntegrationGrant = (id: string) =>
  request(`/grants/${encodeURIComponent(id)}`, { method: "DELETE" });
export const reduceIntegrationGrant = (id: string, scopes: string[]) =>
  request(`/grants/${encodeURIComponent(id)}`, {
    method: "PATCH",
    body: JSON.stringify({ scopes }),
  });
export const fetchIntegrationPolicy = () =>
  request<IntegrationPolicy>("/policy");
export const saveIntegrationPolicy = (
  policy: Omit<IntegrationPolicy, "clients">,
) => request("/policy", { method: "PUT", body: JSON.stringify(policy) });
export const saveIntegrationClient = (client: Omit<IntegrationClient, "id">) =>
  request<IntegrationClient>("/clients", {
    method: "PUT",
    body: JSON.stringify(client),
  });
