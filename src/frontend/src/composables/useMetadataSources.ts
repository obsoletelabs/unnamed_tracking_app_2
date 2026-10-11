import { ref, reactive, onMounted } from "vue";

import { currentUser, checkAuth } from "../state/auth";
import { updateProfile } from "../services/auth";
import {
  fetchScanSettings,
  updateScanSettings,
  fetchAppIntegrations,
  updateAppIntegrations,
} from "../services/settings";
interface CardVisual {
  bg: string;
  fg: string;
  mark: string;
}
// Colored monogram badges shared by the metadata, connection and plugin tiles.
export const SOURCE_VISUALS: Record<string, CardVisual> = {
  Steam: { bg: "#12202e", fg: "#66c0f4", mark: "S" },
  SteamGridDB: { bg: "#0e3b3b", fg: "#2dd4bf", mark: "Gr" },
  IGDB: { bg: "#2b1c4a", fg: "#a78bfa", mark: "IG" },
  TMDB: { bg: "#01283d", fg: "#5dd9c1", mark: "TM" },
  OMDb: { bg: "#2a2205", fg: "#f5c518", mark: "OM" },
  TVDB: { bg: "#1a2a3d", fg: "#7ba7d9", mark: "TV" },
  GiantBomb: { bg: "#3d2f00", fg: "#fbbf24", mark: "GB" },
  RetroAchievements: { bg: "#3b0a0a", fg: "#f87171", mark: "RA" },
  ScreenScraper: { bg: "#1a3d0a", fg: "#86efac", mark: "SS" },
  Xbox: { bg: "#0a2e0a", fg: "#4ade80", mark: "Xb" },
  GOG: { bg: "#2a1a3d", fg: "#c084fc", mark: "GOG" },
  LaunchBox: { bg: "#1a1a1a", fg: "#999999", mark: "LB" },
  PlayStation: { bg: "#0a1a3d", fg: "#60a5fa", mark: "PS" },
  HowLongToBeat: { bg: "#1f1f1f", fg: "#d1d5db", mark: "HL" },
};

export function useMetadataSources() {
  // short one-liner shown on every tile so the grid stays scannable without
  // hovering, the fuller description still lives in the `title` tooltip
  const SHORT_DESC: Record<string, string> = {
    Steam: "Public store data, no key needed",
    SteamGridDB: "Cover art & hero banners",
    IGDB: "General metadata & art (app-wide key)",
    TMDB: "Movie metadata & posters (app-wide key)",
    OMDb: "IMDb-backed movie fallback source (app-wide key)",
    TVDB: "TV show franchise & relations data (app-wide key)",
    GiantBomb: "General metadata & art",
    ScreenScraper: "Retro box art & screenshots",
    GOG: "Metadata search, no key needed",
    LaunchBox: "Local app, not a cloud API",
    RetroAchievements: "Retro metadata & achievements",
    PlayStation: "Trophies & PSN Store data",
    Xbox: "Saved only: no live pull yet",
    HowLongToBeat: "Time-to-beat data",
  };

  const VISUALS = SOURCE_VISUALS;

  // which cards are expanded to show their configure form, collapsed by
  // default so the grid stays a dense, scannable wall of tiles
  const expanded = reactive<Record<string, boolean>>({});
  function toggleExpanded(key: string) {
    expanded[key] = !expanded[key];
  }

  const steamgriddbApiKey = ref(currentUser.value?.steamgriddb_api_key ?? "");
  const saving = ref(false);
  const saveError = ref<string | null>(null);
  const saveSuccess = ref(false);

  async function saveSteamgriddbKey() {
    saving.value = true;
    saveError.value = null;
    saveSuccess.value = false;

    try {
      await updateProfile({
        steamgriddbApiKey: steamgriddbApiKey.value.trim(),
      });
      await checkAuth();
      saveSuccess.value = true;
    } catch (err) {
      saveError.value =
        err instanceof Error ? err.message : "Failed to save settings";
    } finally {
      saving.value = false;
    }
  }

  // IGDB, deployment-wide, admin-only credentials (not per-user, see
  // database/models/app_integration_settings.py). Non-admins never see the
  // form, only whether it's configured.
  const isAdmin = ref(currentUser.value?.is_admin ?? false);
  const igdbClientId = ref("");
  const igdbClientSecret = ref("");
  const igdbConfigured = ref(false);
  const igdbLoading = ref(true);
  const igdbSaving = ref(false);
  const igdbError = ref<string | null>(null);

  const tmdbApiKey = ref("");
  const tmdbConfigured = ref(false);
  const tmdbSaving = ref(false);
  const tmdbError = ref<string | null>(null);

  const omdbApiKey = ref("");
  const omdbConfigured = ref(false);
  const omdbSaving = ref(false);
  const omdbError = ref<string | null>(null);

  // a key the server's environment provides is in effect, but Settings can
  // only override it, not clear it
  const keySources = ref<Record<string, string>>({});
  function keyPlaceholder(name: string, configured: boolean, empty: string) {
    if (!configured) return empty;
    return keySources.value[name] === "environment"
      ? "Set by the server: type a key to override it"
      : "Saved: leave blank to keep";
  }

  const tvdbApiKey = ref("");
  const tvdbConfigured = ref(false);
  const tvdbSaving = ref(false);
  const tvdbError = ref<string | null>(null);

  onMounted(async () => {
    if (!isAdmin.value) {
      igdbLoading.value = false;
      return;
    }
    try {
      const result = await fetchAppIntegrations();
      igdbClientId.value = result.igdb_client_id ?? "";
      igdbConfigured.value = result.igdb_configured;
      keySources.value = result.sources ?? {};
      tmdbConfigured.value = result.tmdb_configured;
      omdbConfigured.value = result.omdb_configured;
      tvdbConfigured.value = result.tvdb_configured;
    } finally {
      igdbLoading.value = false;
    }
  });

  async function saveIgdbCredentials() {
    igdbSaving.value = true;
    igdbError.value = null;
    try {
      const payload: { igdb_client_id?: string; igdb_client_secret?: string } =
        {
          igdb_client_id: igdbClientId.value.trim(),
        };
      if (igdbClientSecret.value.trim())
        payload.igdb_client_secret = igdbClientSecret.value.trim();
      const result = await updateAppIntegrations(payload);
      igdbClientId.value = result.igdb_client_id ?? "";
      igdbConfigured.value = result.igdb_configured;
      igdbClientSecret.value = "";
    } catch (err) {
      igdbError.value =
        err instanceof Error ? err.message : "Failed to save IGDB credentials";
    } finally {
      igdbSaving.value = false;
    }
  }

  async function clearIgdbCredentials() {
    igdbSaving.value = true;
    igdbError.value = null;
    try {
      const result = await updateAppIntegrations({
        igdb_client_id: "",
        igdb_client_secret: "",
      });
      keySources.value = result.sources ?? {};
      igdbClientId.value = result.igdb_client_id ?? "";
      igdbClientSecret.value = "";
      igdbConfigured.value = result.igdb_configured;
    } catch (err) {
      igdbError.value =
        err instanceof Error ? err.message : "Failed to clear IGDB credentials";
    } finally {
      igdbSaving.value = false;
    }
  }

  // TMDB/OMDb each save/clear only their own field via PUT — unlike IGDB's
  // DELETE above, which clears every app-integration field at once. Reusing
  // that same DELETE for a single-key provider would wipe the other two
  // providers' keys too, so a plain PUT with an empty string is used to
  // clear just one field instead.
  async function saveTmdbKey() {
    tmdbSaving.value = true;
    tmdbError.value = null;
    try {
      const result = await updateAppIntegrations({
        tmdb_api_key: tmdbApiKey.value.trim(),
      });
      tmdbConfigured.value = result.tmdb_configured;
      tmdbApiKey.value = "";
    } catch (err) {
      tmdbError.value =
        err instanceof Error ? err.message : "Failed to save TMDB key";
    } finally {
      tmdbSaving.value = false;
    }
  }

  async function clearTmdbKey() {
    tmdbSaving.value = true;
    tmdbError.value = null;
    try {
      const result = await updateAppIntegrations({ tmdb_api_key: "" });
      tmdbApiKey.value = "";
      keySources.value = result.sources ?? {};
      tmdbConfigured.value = result.tmdb_configured;
    } catch (err) {
      tmdbError.value =
        err instanceof Error ? err.message : "Failed to clear TMDB key";
    } finally {
      tmdbSaving.value = false;
    }
  }

  async function saveOmdbKey() {
    omdbSaving.value = true;
    omdbError.value = null;
    try {
      const result = await updateAppIntegrations({
        omdb_api_key: omdbApiKey.value.trim(),
      });
      omdbConfigured.value = result.omdb_configured;
      omdbApiKey.value = "";
    } catch (err) {
      omdbError.value =
        err instanceof Error ? err.message : "Failed to save OMDb key";
    } finally {
      omdbSaving.value = false;
    }
  }

  async function clearOmdbKey() {
    omdbSaving.value = true;
    omdbError.value = null;
    try {
      const result = await updateAppIntegrations({ omdb_api_key: "" });
      omdbApiKey.value = "";
      keySources.value = result.sources ?? {};
      omdbConfigured.value = result.omdb_configured;
    } catch (err) {
      omdbError.value =
        err instanceof Error ? err.message : "Failed to clear OMDb key";
    } finally {
      omdbSaving.value = false;
    }
  }

  async function saveTvdbKey() {
    tvdbSaving.value = true;
    tvdbError.value = null;
    try {
      const result = await updateAppIntegrations({
        tvdb_api_key: tvdbApiKey.value.trim(),
      });
      tvdbConfigured.value = result.tvdb_configured;
      tvdbApiKey.value = "";
    } catch (err) {
      tvdbError.value =
        err instanceof Error ? err.message : "Failed to save TVDB key";
    } finally {
      tvdbSaving.value = false;
    }
  }

  async function clearTvdbKey() {
    tvdbSaving.value = true;
    tvdbError.value = null;
    try {
      const result = await updateAppIntegrations({ tvdb_api_key: "" });
      tvdbApiKey.value = "";
      keySources.value = result.sources ?? {};
      tvdbConfigured.value = result.tvdb_configured;
    } catch (err) {
      tvdbError.value =
        err instanceof Error ? err.message : "Failed to clear TVDB key";
    } finally {
      tvdbSaving.value = false;
    }
  }

  // HowLongToBeat, a real toggle now, not just informational. "Enabled"
  // means "HowLongToBeat" is present in the user's scan provider_order.
  const hltbEnabled = ref(false);
  const hltbLoading = ref(true);
  const hltbSaving = ref(false);
  const hltbError = ref<string | null>(null);

  onMounted(async () => {
    try {
      const scan = await fetchScanSettings();
      hltbEnabled.value = scan.provider_order.includes("HowLongToBeat");
    } finally {
      hltbLoading.value = false;
    }
  });

  async function toggleHltb(enabled: boolean) {
    hltbSaving.value = true;
    hltbError.value = null;
    try {
      const scan = await fetchScanSettings();
      const nextOrder = enabled
        ? [
            ...scan.provider_order.filter((p) => p !== "HowLongToBeat"),
            "HowLongToBeat",
          ]
        : scan.provider_order.filter((p) => p !== "HowLongToBeat");
      await updateScanSettings({
        provider_order: nextOrder as typeof scan.provider_order,
      });
      hltbEnabled.value = enabled;
    } catch (err) {
      hltbError.value =
        err instanceof Error ? err.message : "Failed to update HowLongToBeat";
    } finally {
      hltbSaving.value = false;
    }
  }

  return {
    SHORT_DESC,
    VISUALS,
    expanded,
    toggleExpanded,
    steamgriddbApiKey,
    saving,
    saveError,
    saveSuccess,
    saveSteamgriddbKey,
    isAdmin,
    igdbClientId,
    igdbClientSecret,
    igdbConfigured,
    igdbSaving,
    igdbError,
    tmdbApiKey,
    tmdbConfigured,
    tmdbSaving,
    tmdbError,
    omdbApiKey,
    omdbConfigured,
    omdbSaving,
    omdbError,
    keyPlaceholder,
    tvdbApiKey,
    tvdbConfigured,
    tvdbSaving,
    tvdbError,
    saveIgdbCredentials,
    clearIgdbCredentials,
    saveTmdbKey,
    clearTmdbKey,
    saveOmdbKey,
    clearOmdbKey,
    saveTvdbKey,
    clearTvdbKey,
    hltbEnabled,
    hltbLoading,
    hltbSaving,
    hltbError,
    toggleHltb,
  };
}
