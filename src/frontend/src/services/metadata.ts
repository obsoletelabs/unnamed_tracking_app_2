import type { GameLink } from "../types/game";
import { failedRequest } from "./apiError";

export type MetadataMediaType = "game" | "movie" | "tv_show" | "anime";

export interface MetadataProviderStatus {
  provider_id: string;
  plugin_id: string;
  included?: boolean;
  account_enabled?: boolean;
  // Effective ranks from this account's saved orders; lower is used first.
  priority?: number;
  artwork_priority?: number;
  automatic_search?: {
    enabled: boolean;
    account_enabled: boolean;
    provider_enabled: boolean;
    server_enabled: boolean;
    server_provider_enabled: boolean;
    uses_personal_credentials: boolean;
    reason:
      | "account_disabled"
      | "provider_disabled"
      | "server_disabled"
      | "metadata_provider_disabled"
      | null;
  };
  name: string;
  media_types: MetadataMediaType[];
  state: string;
  checked_at: number | null;
  failure: string | null;
  configured_fields: Record<string, { system: boolean; user: boolean }>;
  operations: Record<"search" | "metadata" | "media" | "health", string | null>;
  configuration: {
    key: string;
    label: string;
    scope: "system" | "user" | "both";
    required: boolean;
    secret: boolean;
  }[];
}

export async function fetchServerAutomaticSearch(): Promise<boolean> {
  const response = await fetch("/api/metadata/automatic-search", {
    credentials: "include",
  });
  if (!response.ok) throw await failedRequest(response);
  return (await response.json()).enabled;
}

export async function saveServerAutomaticSearch(
  enabled: boolean,
  providerId: string | null = null,
): Promise<void> {
  const response = await fetch("/api/metadata/automatic-search", {
    method: "PUT",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ enabled, provider_id: providerId }),
  });
  if (!response.ok) throw await failedRequest(response);
}

export async function fetchMetadataProviders(): Promise<
  MetadataProviderStatus[]
> {
  const response = await fetch("/api/metadata/providers", {
    credentials: "include",
  });
  if (!response.ok) throw await failedRequest(response);
  return (await response.json()).providers;
}

export async function saveMetadataProviderConfiguration(
  providerId: string,
  scope: "system" | "user",
  values: Record<string, string | null>,
): Promise<void> {
  const response = await fetch(
    `/api/metadata/providers/${encodeURIComponent(providerId)}/configuration`,
    {
      method: "PUT",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ scope, values }),
    },
  );
  if (!response.ok) throw await failedRequest(response);
}

export interface MetadataAsset {
  kind:
    "key_art" | "banner" | "logo" | "icon" | "screenshot" | "hero" | "poster";
  url: string;
  width: number | null;
  height: number | null;
}

export interface CanonicalMetadata {
  title?: string | null;
  year?: number | null;
  description?: string | null;
  release_date?: string | null;
  developer?: string | null;
  publisher?: string | null;
  series?: string | null;
  age_rating?: string | null;
  time_to_beat_hours?: number | null;
  time_to_beat_extras_hours?: number | null;
  time_to_beat_completionist_hours?: number | null;
  time_to_beat_all_hours?: number | null;
  tags?: string[];
  features?: string[];
  genres?: string[];
  platforms?: string[];
  provider_ids?: Record<string, string>;
  links?: { label: string; url: string }[];
  runtime_minutes?: number | null;
  director?: string | null;
  writer?: string | null;
  creators?: string[];
  studios?: string[];
  countries?: string[];
  languages?: string[];
  episode_runtime_minutes?: number | null;
  episode_count?: number | null;
  format?: string | null;
  scores?: Record<string, number>;
  titles?: Record<string, string>;
  seasons?: {
    season_number: number;
    title: string | null;
    episode_count: number | null;
    air_date: string | null;
  }[];
}

export interface MetadataCandidate {
  id: string;
  rank: number;
  external_id: string;
  title: string;
  year: number | null;
  provider: string;
  provider_name: string;
  providers: string[];
  provider_ids: Record<string, string>;
  media_type: MetadataMediaType;
  alternate_titles: string[];
  metadata: CanonicalMetadata;
  assets: MetadataAsset[];
}

export interface MetadataSession {
  id: string;
  query: string;
  media_type: MetadataMediaType;
  state: string;
  results: MetadataCandidate[];
  last_event_id: number;
}

export interface MetadataEvent {
  id: number;
  event: string;
  session_id: string;
  result?: MetadataCandidate;
  candidate_id?: string;
  candidate_ids?: string[];
  replacement_id?: string | null;
  message?: string;
}

export async function startMetadataSearch(
  query: string,
  mediaType: MetadataMediaType,
  signal?: AbortSignal,
  trigger: "explicit" | "automatic" = "explicit",
): Promise<MetadataSession> {
  const response = await fetch("/api/metadata/sessions", {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query, media_type: mediaType, trigger }),
    signal,
  });
  if (!response.ok) throw await failedRequest(response);
  return response.json();
}

export async function cancelMetadataSearch(sessionId: string): Promise<void> {
  await fetch(`/api/metadata/sessions/${encodeURIComponent(sessionId)}`, {
    method: "DELETE",
    credentials: "include",
  });
}

export async function selectMetadataCandidate(
  sessionId: string,
  candidateId: string,
): Promise<MetadataCandidate> {
  const response = await fetch(
    `/api/metadata/sessions/${encodeURIComponent(sessionId)}/selection`,
    {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ candidate_id: candidateId }),
    },
  );
  if (!response.ok) throw await failedRequest(response);
  return response.json();
}

export function metadataEvents(sessionId: string, after = 0): EventSource {
  return new EventSource(
    `/api/metadata/sessions/${encodeURIComponent(sessionId)}/events?after=${after}`,
    { withCredentials: true },
  );
}

export async function previewMetadataArtwork(
  sessionId: string,
  candidateId: string,
  url: string,
): Promise<Blob> {
  const response = await fetch(
    `/api/metadata/sessions/${encodeURIComponent(sessionId)}/artwork`,
    {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ candidate_id: candidateId, url }),
    },
  );
  if (!response.ok) throw await failedRequest(response);
  return response.blob();
}

export async function focusMetadataEntity(
  title: string,
  mediaType: MetadataMediaType,
  providerIds: Record<string, string>,
): Promise<MetadataSession> {
  const response = await fetch("/api/metadata/selection", {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      candidate: {
        title,
        provider: "local",
        external_id: title,
        media_type: mediaType,
        provider_ids: providerIds,
      },
    }),
  });
  if (!response.ok) throw await failedRequest(response);
  return response.json();
}

export interface MetadataSearchResult {
  release_year?: number | null;
  candidate_id?: string;
  provider_ids?: Record<string, string>;
  provider: string;
  provider_id: string;
  title: string;
  description: string | null;
  release_date: string | null;
  developer: string | null;
  publisher: string | null;
  series: string | null;
  age_rating: string | null;
  time_to_beat_hours: number | string | null;
  time_to_beat_extras_hours?: number | string | null;
  time_to_beat_completionist_hours?: number | string | null;
  time_to_beat_all_hours?: number | string | null;
  tags: string[];
  features: string[];
  links: GameLink[];
  key_art_url: string | null;
  key_art_urls: string[];
  banner_url: string | null;
  banner_urls: string[];
  logo_url: string | null;
  logo_urls: string[];
  icon_url: string | null;
  icon_urls: string[];
}
