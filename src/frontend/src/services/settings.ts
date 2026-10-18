export interface ScanSettings {
  id: string;
  user_id: string;
  // Metadata provider IDs ("core.igdb", "<plugin>.<provider>") shared by core and
  // plugin providers; orders saved before plugin providers may hold display names.
  provider_order: string[];
  image_provider_order: string[];
  save_developer: boolean;
  save_publisher: boolean;
  save_series: boolean;
  save_tags: boolean;
  save_features: boolean;
  save_description: boolean;
  save_age_rating: boolean;
  save_release_date: boolean;
  save_time_to_beat: boolean;
  save_key_art: boolean;
  save_banner: boolean;
  save_logo: boolean;
  save_icon: boolean;
  // {provider ID: epoch seconds} (older rows may use display names), last time
  // that provider supplied a chosen result; read-only, not in ScanSettingsUpdate
  provider_last_used: Record<string, number>;
  created_at: number;
  updated_at: number;
}

export type ScanSettingsUpdate = Partial<
  Pick<
    ScanSettings,
    | "provider_order"
    | "image_provider_order"
    | "save_developer"
    | "save_publisher"
    | "save_series"
    | "save_tags"
    | "save_features"
    | "save_description"
    | "save_age_rating"
    | "save_release_date"
    | "save_time_to_beat"
    | "save_key_art"
    | "save_banner"
    | "save_logo"
    | "save_icon"
  >
>;

const MOCK_SCAN_SETTINGS: ScanSettings = {
  id: "mock",
  user_id: "mock",
  provider_order: [
    "IGDB",
    "GiantBomb",
    "Steam",
    "GOG",
    "RetroAchievements",
    "HowLongToBeat",
  ],
  image_provider_order: ["SteamGridDB", "ScreenScraper"],
  save_developer: true,
  save_publisher: true,
  save_series: true,
  save_tags: true,
  save_features: true,
  save_description: true,
  save_age_rating: true,
  save_release_date: true,
  save_time_to_beat: true,
  save_key_art: true,
  save_banner: true,
  save_logo: true,
  save_icon: true,
  provider_last_used: {},
  created_at: 0,
  updated_at: 0,
};

export async function fetchScanSettings(): Promise<ScanSettings> {
  if (import.meta.env.VITE_USE_MOCK_DATA === "true") {
    return { ...MOCK_SCAN_SETTINGS };
  }

  const response = await fetch("/api/settings/scan", {
    credentials: "include",
  });
  if (!response.ok) {
    throw new Error(
      `Failed to fetch scan settings: ${response.status} ${response.statusText}`,
    );
  }
  return await response.json();
}

export async function updateScanSettings(
  payload: ScanSettingsUpdate,
): Promise<ScanSettings> {
  if (import.meta.env.VITE_USE_MOCK_DATA === "true") {
    Object.assign(MOCK_SCAN_SETTINGS, payload);
    return { ...MOCK_SCAN_SETTINGS };
  }

  const response = await fetch("/api/settings/scan", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    const message = await response.text();
    throw new Error(
      `Failed to update scan settings: ${response.status} ${response.statusText} ${message}`,
    );
  }
  return await response.json();
}

export interface UploadLimits {
  max_upload_size_mb: number;
  max_doc_size_mb: number;
  max_save_archive_size_mb: number;
  max_clip_size_mb: number;
  max_world_save_size_mb: number;
}
const defaultUploadLimits: UploadLimits = {
  max_upload_size_mb: 15,
  max_doc_size_mb: 500,
  max_save_archive_size_mb: 4096,
  max_clip_size_mb: 500,
  max_world_save_size_mb: 2000,
};

/** Where one upload cap's value comes from, and whether the environment locks it (#253). */
export interface UploadLimitSource {
  value: number;
  source: "environment" | "override" | "default";
  locked: boolean;
  environment_variable: string;
  environment_value: number | null;
  /** The variable is set but is not a whole number of megabytes, so it was ignored. */
  environment_invalid: boolean;
  stored_override: number | null;
}
export type UploadLimitSources = Record<keyof UploadLimits, UploadLimitSource>;

export async function fetchUploadLimitSources(): Promise<UploadLimitSources> {
  if (import.meta.env.VITE_USE_MOCK_DATA === "true") {
    return Object.fromEntries(
      (Object.keys(defaultUploadLimits) as (keyof UploadLimits)[]).map(
        (key) => [
          key,
          {
            value: defaultUploadLimits[key],
            source: "default",
            locked: false,
            environment_variable: key.toUpperCase(),
            environment_value: null,
            environment_invalid: false,
            stored_override: null,
          },
        ],
      ),
    ) as UploadLimitSources;
  }
  const response = await fetch("/api/settings/upload-limit-sources", {
    credentials: "include",
  });
  if (!response.ok)
    throw new Error(`Failed to fetch upload limits: ${response.status}`);
  return response.json();
}

export async function fetchUploadLimits(): Promise<UploadLimits> {
  if (import.meta.env.VITE_USE_MOCK_DATA === "true")
    return { ...defaultUploadLimits };
  const response = await fetch("/api/settings/upload-limits", {
    credentials: "include",
  });
  if (!response.ok)
    throw new Error(`Failed to fetch upload limits: ${response.status}`);
  return response.json();
}

export async function updateUploadLimits(
  values: Partial<Record<keyof UploadLimits, number | null>>,
): Promise<UploadLimits> {
  if (import.meta.env.VITE_USE_MOCK_DATA === "true") {
    const result = { ...defaultUploadLimits };
    for (const key of Object.keys(values) as (keyof UploadLimits)[])
      result[key] = values[key] ?? defaultUploadLimits[key];
    return result;
  }
  const response = await fetch("/api/settings/upload-limit", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: JSON.stringify(values),
  });
  if (!response.ok)
    throw new Error(
      `Failed to update upload limits: ${response.status} ${await response.text()}`,
    );
  return response.json();
}

export async function updateUploadLimit(
  maxUploadSizeMb: number | null,
): Promise<UploadLimits> {
  return updateUploadLimits({ max_upload_size_mb: maxUploadSizeMb });
}

export interface SystemInfo {
  server_time: number;
  uptime_seconds: number;
  debug: boolean;
  python_version: string;
  platform: string;
}

// Admin-only, for Settings > Administration > Dev Tools.
export async function fetchSystemInfo(): Promise<SystemInfo> {
  if (import.meta.env.VITE_USE_MOCK_DATA === "true") {
    return {
      server_time: Math.floor(Date.now() / 1000),
      uptime_seconds: 3600,
      debug: false,
      python_version: "3.12.0",
      platform: "Mock-Platform",
    };
  }

  const response = await fetch("/api/settings/system-info", {
    credentials: "include",
  });
  if (!response.ok) {
    throw new Error(
      `Failed to fetch system info: ${response.status} ${response.statusText}`,
    );
  }
  return await response.json();
}

export interface ProviderCredentialStatus {
  status: "not_configured" | "configured" | "connected" | "saved" | "error";
  detail?: string | null;
  connection_method?: "account";
  app_configured?: boolean;
  // a server-wide key (Server Integrations or the environment) covers this
  // provider for anyone without their own
  server_configured?: boolean;
  // library-sync providers only (Steam, RetroAchievements, PlayStation)
  library_games?: number;
  last_synced_at?: number | null;
  // who's connected, when known (Steam, RetroAchievements, PlayStation)
  display_name?: string | null;
  avatar_url?: string | null;
  // non-secret field values already saved (steam_id, username, ssid,
  // client_id) so the form can show them filled instead of blank
  fields?: Record<string, string>;
}

const MOCK_PROVIDER_CREDENTIALS: Record<string, ProviderCredentialStatus> = {};

export async function fetchProviderCredentials(): Promise<
  Record<string, ProviderCredentialStatus>
> {
  if (import.meta.env.VITE_USE_MOCK_DATA === "true") {
    return { ...MOCK_PROVIDER_CREDENTIALS };
  }

  const response = await fetch("/api/settings/provider-credentials", {
    credentials: "include",
  });
  if (!response.ok) {
    throw new Error(
      `Failed to fetch provider credentials: ${response.status} ${response.statusText}`,
    );
  }
  return await response.json();
}

export async function saveProviderCredentials(
  provider: string,
  fields: Record<string, string>,
): Promise<ProviderCredentialStatus> {
  if (import.meta.env.VITE_USE_MOCK_DATA === "true") {
    const result: ProviderCredentialStatus = { status: "connected" };
    MOCK_PROVIDER_CREDENTIALS[provider] = { status: "configured" };
    return result;
  }

  const response = await fetch(
    `/api/settings/provider-credentials/${encodeURIComponent(provider)}`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify({ fields }),
    },
  );
  if (!response.ok) {
    const message = await response.text();
    throw new Error(
      `Failed to save ${provider} credentials: ${response.status} ${response.statusText} ${message}`,
    );
  }
  return await response.json();
}

export async function deleteProviderCredentials(
  provider: string,
): Promise<void> {
  if (import.meta.env.VITE_USE_MOCK_DATA === "true") {
    delete MOCK_PROVIDER_CREDENTIALS[provider];
    return;
  }

  const response = await fetch(
    `/api/settings/provider-credentials/${encodeURIComponent(provider)}`,
    {
      method: "DELETE",
      credentials: "include",
    },
  );
  if (!response.ok) {
    throw new Error(
      `Failed to disconnect ${provider}: ${response.status} ${response.statusText}`,
    );
  }
}

// Deployment-wide (not per-user) integration credentials, admin-only.
// An IGDB/Twitch developer app is registered once per self-hosted
// instance and entered here, not baked into .env, so a downloaded copy of
// this app never ships with someone else's credentials.
export interface AppIntegrationSettings {
  igdb_client_id: string | null;
  igdb_configured: boolean;
  tmdb_configured: boolean;
  omdb_configured: boolean;
  tvdb_configured: boolean;
  // where each key in effect comes from: Settings, or the server environment
  sources?: Record<string, "database" | "environment">;
}

export async function fetchAppIntegrations(): Promise<AppIntegrationSettings> {
  if (import.meta.env.VITE_USE_MOCK_DATA === "true") {
    return {
      igdb_client_id: null,
      igdb_configured: false,
      tmdb_configured: false,
      omdb_configured: false,
      tvdb_configured: false,
    };
  }
  const response = await fetch("/api/settings/app-integrations", {
    credentials: "include",
  });
  if (!response.ok) {
    throw new Error(
      `Failed to fetch app integrations: ${response.status} ${response.statusText}`,
    );
  }
  return await response.json();
}

export async function updateAppIntegrations(payload: {
  igdb_client_id?: string;
  igdb_client_secret?: string;
  tmdb_api_key?: string;
  omdb_api_key?: string;
  tvdb_api_key?: string;
}): Promise<AppIntegrationSettings> {
  if (import.meta.env.VITE_USE_MOCK_DATA === "true") {
    return {
      igdb_client_id: payload.igdb_client_id ?? null,
      igdb_configured: true,
      tmdb_configured: !!payload.tmdb_api_key,
      omdb_configured: !!payload.omdb_api_key,
      tvdb_configured: !!payload.tvdb_api_key,
    };
  }
  const response = await fetch("/api/settings/app-integrations", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    const message = await response.text();
    throw new Error(
      `Failed to save app integrations: ${response.status} ${response.statusText} ${message}`,
    );
  }
  return await response.json();
}

export interface MediaRefreshProgress {
  running: boolean;
  mode: "needed" | "all";
  phase: string;
  total: number;
  done: number;
  current: string;
  checked: number;
  skippedUpToDate: number;
  animeEpisodesAdded: number;
  animeEpisodesUpdated: number;
  tvEpisodesAdded: number;
  tvEpisodesUpdated: number;
  countsFixed: number;
  animeMetadataHealed: number;
  unreachable: string[];
  startedAt: number | null;
  finishedAt: number | null;
  error: string | null;
}

interface BackendRefreshProgress {
  running: boolean;
  mode: "needed" | "all";
  phase: string;
  total: number;
  done: number;
  current: string;
  checked: number;
  skipped_up_to_date: number;
  anime_episodes_added: number;
  anime_episodes_updated: number;
  tv_episodes_added: number;
  tv_episodes_updated: number;
  counts_fixed: number;
  anime_metadata_healed: number;
  unreachable: string[];
  started_at: number | null;
  finished_at: number | null;
  error: string | null;
}

function mapRefreshProgress(d: BackendRefreshProgress): MediaRefreshProgress {
  return {
    running: d.running,
    mode: d.mode,
    phase: d.phase,
    total: d.total,
    done: d.done,
    current: d.current,
    checked: d.checked,
    skippedUpToDate: d.skipped_up_to_date,
    animeEpisodesAdded: d.anime_episodes_added,
    animeEpisodesUpdated: d.anime_episodes_updated,
    tvEpisodesAdded: d.tv_episodes_added,
    tvEpisodesUpdated: d.tv_episodes_updated,
    countsFixed: d.counts_fixed,
    animeMetadataHealed: d.anime_metadata_healed,
    unreachable: d.unreachable,
    startedAt: d.started_at,
    finishedAt: d.finished_at,
    error: d.error,
  };
}

// Starts the episode refresh in the background and returns at once; poll
// fetchMediaRefreshProgress for how far it has got. "needed" only touches
// titles that need it, "all" checks every title.
export async function startMediaRefresh(
  mode: "needed" | "all",
): Promise<MediaRefreshProgress> {
  const response = await fetch(
    `/api/settings/refresh-media-metadata?mode=${mode}`,
    { method: "POST", credentials: "include" },
  );
  if (!response.ok) {
    throw new Error(`Failed to start the refresh: ${response.status}`);
  }
  return mapRefreshProgress(await response.json());
}

export async function fetchMediaRefreshProgress(): Promise<MediaRefreshProgress> {
  const response = await fetch("/api/settings/refresh-media-progress", {
    credentials: "include",
  });
  if (!response.ok) {
    throw new Error(`Failed to read the refresh progress: ${response.status}`);
  }
  return mapRefreshProgress(await response.json());
}

// Jobs the app runs on a schedule (see backend features/jobs.py). The airing
// check starts on, the heavy media refresh starts off.
export interface CleanupJob {
  id: string;
  name: string;
  description: string;
  enabled: boolean;
  intervalMinutes: number;
  minIntervalMinutes: number;
  maxIntervalMinutes: number;
  lastRunAt: number | null;
  lastSummary: string;
  lastResult: Record<string, number | string | boolean | null>;
  running: boolean;
  pluginId: string | null;
  pluginName: string | null;
  available: boolean;
  unavailableReason: string;
  paused: boolean;
  status: string;
  lastSuccessAt: number | null;
  nextRunAt: number | null;
  failureCount: number;
}
interface BackendJob {
  id: string;
  name: string;
  description: string;
  enabled: boolean;
  interval_minutes: number;
  min_interval_minutes: number;
  max_interval_minutes: number;
  last_run_at: number | null;
  last_summary: string;
  last_result: Record<string, number | string | boolean | null>;
  running: boolean;
  plugin_id?: string | null;
  plugin_name?: string | null;
  available?: boolean;
  unavailable_reason?: string;
  paused?: boolean;
  status?: string;
  last_success_at?: number | null;
  next_run_at?: number | null;
  failure_count?: number;
}
function mapJob(j: BackendJob): CleanupJob {
  return {
    id: j.id,
    name: j.name,
    description: j.description,
    enabled: j.enabled,
    intervalMinutes: j.interval_minutes,
    minIntervalMinutes: j.min_interval_minutes,
    maxIntervalMinutes: j.max_interval_minutes,
    lastRunAt: j.last_run_at,
    lastSummary: j.last_summary,
    lastResult: j.last_result,
    running: j.running,
    pluginId: j.plugin_id ?? null,
    pluginName: j.plugin_name ?? null,
    available: j.available !== false,
    unavailableReason: j.unavailable_reason ?? "",
    paused: j.paused === true,
    status: j.status ?? "idle",
    lastSuccessAt: j.last_success_at ?? null,
    nextRunAt: j.next_run_at ?? null,
    failureCount: j.failure_count ?? 0,
  };
}
export async function fetchJobs(personal = false): Promise<CleanupJob[]> {
  const response = await fetch(
    `/api/settings/${personal ? "user-jobs" : "jobs"}`,
    {
      credentials: "include",
    },
  );
  if (!response.ok)
    throw new Error(`Failed to load the jobs: ${response.status}`);
  return ((await response.json()) as BackendJob[]).map(mapJob);
}
export async function updateJob(
  id: string,
  changes: { enabled?: boolean; intervalMinutes?: number },
  personal = false,
): Promise<CleanupJob> {
  const response = await fetch(
    `/api/settings/${personal ? "user-jobs" : "jobs"}/${id}`,
    {
      method: "PUT",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        enabled: changes.enabled,
        interval_minutes: changes.intervalMinutes,
      }),
    },
  );
  if (!response.ok)
    throw new Error(await jobErrorDetail(response, "Failed to save the job"));
  return mapJob(await response.json());
}
export type JobControl = "pause" | "resume" | "cancel";
export async function controlJob(
  id: string,
  control: JobControl,
  personal = false,
): Promise<CleanupJob> {
  const response = await fetch(
    `/api/settings/${personal ? "user-jobs" : "jobs"}/${id}/${control}`,
    { method: "POST", credentials: "include" },
  );
  if (!response.ok)
    throw new Error(await jobErrorDetail(response, `Failed to ${control}`));
  return mapJob(await response.json());
}
export async function runJobNow(id: string, personal = false): Promise<void> {
  const response = await fetch(
    `/api/settings/${personal ? "user-jobs" : "jobs"}/${id}/run`,
    {
      method: "POST",
      credentials: "include",
    },
  );
  if (!response.ok)
    throw new Error(await jobErrorDetail(response, "Failed to start the job"));
}

async function jobErrorDetail(
  response: Response,
  fallback: string,
): Promise<string> {
  const body = await response.json().catch(() => null);
  return typeof body?.detail === "string"
    ? body.detail
    : `${fallback}: ${response.status}`;
}
