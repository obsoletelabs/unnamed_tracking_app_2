import { reactive, ref } from "vue";

import { fetchPsnStatus, connectPsn, disconnectPsn } from "../services/psn";
import type { PsnStatus } from "../services/psn";
import {
  fetchProviderCredentials,
  saveProviderCredentials,
  deleteProviderCredentials,
} from "../services/settings";
import type { ProviderCredentialStatus } from "../services/settings";
import { syncLibrary } from "../services/librarySync";
import type { LibrarySyncProvider } from "../services/librarySync";
import {
  startTask,
  completeTask,
  errorTask,
  addFeedItem,
} from "../state/taskProgress";

export interface ConnectionFieldConfig {
  key: string;
  label: string;
  type: "text" | "password";
}

export interface ConnectionConfig {
  key: string;
  label: string;
  summary: string;
  description: string;
  // "fields" saves provider credentials, "psn" uses the npsso token flow
  kind: "fields" | "psn";
  fields: ConnectionFieldConfig[];
  syncProvider?: LibrarySyncProvider;
  linkLabel?: string;
  linkUrl?: string;
  // saved but nothing is pulled from the provider yet
  deferred?: boolean;
  // opens a provider-specific sign-in dialog; the credential fields become
  // a collapsed fallback
  signIn?: "steam";
}

// Accounts you sign in to and pull a library from. Metadata sources (IGDB,
// SteamGridDB, TMDB...) live under Library > Metadata.
export const CONNECTIONS: ConnectionConfig[] = [
  {
    key: "Steam",
    label: "Steam",
    summary: "Library & achievements",
    description:
      "Connect Steam with QR or account sign-in for library and private achievement imports. Legacy API credentials remain available for public profiles: both fields are required, and the profile's game details must be Public.",
    kind: "fields",
    fields: [
      { key: "steam_id", label: "Profile ID", type: "text" },
      { key: "api_key", label: "Web API Key", type: "password" },
    ],
    syncProvider: "steam",
    signIn: "steam",
    linkLabel: "Get a Steam Web API key",
    linkUrl: "https://steamcommunity.com/dev/apikey",
  },
  {
    key: "PlayStation",
    label: "PlayStation",
    summary: "Trophies & PSN Store data",
    description:
      "Unofficial npsso token flow. Sign in to playstation.com in your browser, then paste the npsso token here. It also powers PSN Store data.",
    kind: "psn",
    fields: [],
    syncProvider: "psn",
    linkLabel: "Open playstation.com",
    linkUrl: "https://www.playstation.com",
  },
  {
    key: "RetroAchievements",
    label: "RetroAchievements",
    summary: "Retro library & achievements",
    description:
      "Your username and Web API key pull your retro library and unlocked achievements. Metadata credentials are configured separately under Library > Metadata.",
    kind: "fields",
    fields: [
      { key: "username", label: "Username", type: "text" },
      { key: "api_key", label: "API Key", type: "password" },
    ],
    syncProvider: "retroachievements",
    linkLabel: "Get a free key from RetroAchievements",
    linkUrl: "https://retroachievements.org/controlpanel.php",
  },
  {
    key: "Xbox",
    label: "Xbox",
    summary: "Saved only: no live pull yet",
    description:
      "Unofficial: Xbox's real API needs a full sign-in flow this page can't host yet. Saving your Azure app credentials here just gets them ready for that; nothing is pulled from Xbox yet.",
    kind: "fields",
    fields: [
      { key: "client_id", label: "Application (client) ID", type: "text" },
      { key: "client_secret", label: "Client secret", type: "password" },
    ],
    deferred: true,
  },
  {
    key: "GOG",
    label: "GOG",
    summary: "Library import token",
    description:
      "The refresh token below is only for a future library import. GOG metadata search uses the public store catalog and needs no key.",
    kind: "fields",
    fields: [
      {
        key: "refresh_token",
        label: "Refresh token (library import only)",
        type: "password",
      },
    ],
    deferred: true,
  },
];

export const CONNECTION_BY_KEY: Record<string, ConnectionConfig> =
  Object.fromEntries(
    CONNECTIONS.map((connection) => [connection.key, connection]),
  );

const LIBRARY_SYNC_LABELS: Record<LibrarySyncProvider, string> = {
  steam: "Steam",
  psn: "PlayStation",
  retroachievements: "RetroAchievements",
};

export function isConnectionConnected(
  status: ProviderCredentialStatus | undefined,
): boolean {
  return (
    status?.status === "connected" ||
    status?.status === "configured" ||
    status?.status === "saved"
  );
}

export function useConnections() {
  const credentialStatus = reactive<Record<string, ProviderCredentialStatus>>(
    {},
  );
  const credentialsLoading = ref(true);
  const loadError = ref<string | null>(null);

  // must be ready before first render, the dialog binds
  // fieldValues[key][field.key] immediately
  const fieldValues = reactive<Record<string, Record<string, string>>>({});
  const cardSaving = reactive<Record<string, boolean>>({});
  const cardError = reactive<Record<string, string | null>>({});
  const cardInfo = reactive<Record<string, string | null>>({});
  for (const connection of CONNECTIONS) fieldValues[connection.key] = {};

  async function loadCredentials() {
    try {
      const result = await fetchProviderCredentials();
      Object.assign(credentialStatus, result);
      loadError.value = null;
      // prefill non-secret fields (profile IDs, usernames) that are already
      // saved, so the form shows them filled instead of misleadingly blank
      for (const [key, status] of Object.entries(result)) {
        if (status.fields)
          Object.assign(
            fieldValues[key] ?? (fieldValues[key] = {}),
            status.fields,
          );
      }
    } catch (err) {
      loadError.value =
        err instanceof Error ? err.message : "Couldn't load connections.";
    } finally {
      credentialsLoading.value = false;
    }
  }

  // a password-type field that's already saved gets a placeholder instead of
  // its real value (never echoed back), still communicates "this is filled"
  function passwordPlaceholder(
    key: string,
    fieldKey: string,
    label: string,
  ): string {
    if (
      isConnectionConnected(credentialStatus[key]) &&
      !fieldValues[key]?.[fieldKey]
    )
      return "Already saved: paste a new value to replace it";
    return `Paste your ${label.toLowerCase()}`;
  }

  async function saveConnection(connection: ConnectionConfig) {
    cardSaving[connection.key] = true;
    cardError[connection.key] = null;
    cardInfo[connection.key] = null;
    try {
      const result = await saveProviderCredentials(
        connection.key,
        fieldValues[connection.key],
      );
      credentialStatus[connection.key] = result;
      if (result.status === "error") {
        cardError[connection.key] =
          result.detail ?? "Could not verify these credentials.";
      } else if (result.status === "saved" && result.detail) {
        cardInfo[connection.key] = result.detail;
      }
    } catch (err) {
      cardError[connection.key] =
        err instanceof Error ? err.message : "Failed to save";
    } finally {
      cardSaving[connection.key] = false;
    }
  }

  // messages from an earlier dialog visit don't describe the current state
  function clearMessages(key: string) {
    cardError[key] = null;
    cardInfo[key] = null;
    psnError.value = null;
  }

  async function clearConnection(connection: ConnectionConfig) {
    try {
      await deleteProviderCredentials(connection.key);
      credentialStatus[connection.key] = { status: "not_configured" };
      cardError[connection.key] = null;
      cardInfo[connection.key] = null;
      fieldValues[connection.key] = {};
    } catch (err) {
      cardError[connection.key] =
        err instanceof Error ? err.message : "Failed to disconnect";
    }
  }

  // --- PlayStation (npsso token) -----------------------------------------

  const psnStatus = ref<PsnStatus>({ connected: false, validated_at: null });
  const psnLoading = ref(true);
  const npssoToken = ref("");
  const psnConnecting = ref(false);
  const psnError = ref<string | null>(null);

  async function loadPsn() {
    // a stale status from an earlier open must not show as current
    psnLoading.value = true;
    try {
      psnStatus.value = await fetchPsnStatus();
    } catch (err) {
      psnError.value =
        err instanceof Error
          ? err.message
          : "Couldn't load the PlayStation connection.";
    } finally {
      psnLoading.value = false;
    }
  }

  async function handleConnectPsn() {
    if (!npssoToken.value.trim()) {
      psnError.value = "Paste your npsso token first.";
      return;
    }
    if (psnConnecting.value) return;
    psnConnecting.value = true;
    psnError.value = null;
    try {
      psnStatus.value = await connectPsn(npssoToken.value.trim());
      npssoToken.value = "";
      await loadCredentials();
    } catch (err) {
      psnError.value =
        err instanceof Error
          ? err.message
          : "Failed to connect PlayStation account";
    } finally {
      psnConnecting.value = false;
    }
  }

  async function handleDisconnectPsn() {
    psnError.value = null;
    try {
      await disconnectPsn();
      psnStatus.value = { connected: false, validated_at: null };
      await loadCredentials();
    } catch (err) {
      psnError.value =
        err instanceof Error
          ? err.message
          : "Failed to disconnect PlayStation account";
    }
  }

  // --- library sync (real owned-games + achievements pull) ---------------

  const librarySyncing = reactive<Record<LibrarySyncProvider, boolean>>({
    steam: false,
    psn: false,
    retroachievements: false,
  });

  async function handleSyncLibrary(provider: LibrarySyncProvider) {
    librarySyncing[provider] = true;
    // indeterminate, the backend is one all-at-once request with no
    // per-game signal until it resolves, so there's nothing real to show as
    // a fraction while it's in flight
    const taskId = startTask(
      `Syncing ${LIBRARY_SYNC_LABELS[provider]} library`,
      1,
      { indeterminate: true },
    );
    try {
      const result = await syncLibrary(provider);
      const unavailable =
        (result.achievements_unavailable?.length ?? 0) +
        (result.achievements_failed ?? 0);
      const warnings = [
        unavailable &&
          `achievement progress unavailable for ${unavailable} games (stored progress kept)`,
        result.wishlist_failed &&
          `wishlist not imported${result.wishlist_error ? ` (${result.wishlist_error})` : ""}`,
        result.enrich_failed &&
          `store details not filled in for ${result.enrich_failed} games`,
      ].filter((warning): warning is string => !!warning);
      const wishlisted = result.wishlist_added
        ? `, ${result.wishlist_added} wishlisted`
        : "";
      completeTask(
        taskId,
        `${result.games_added} added, ${result.games_updated} updated${wishlisted}, ${result.achievements_synced} achievements${warnings.map((warning) => `; ${warning}`).join("")}`,
      );
      // the request itself wasn't live, but revealing the touched titles one
      // at a time still reads as a real "feed" once the result is in
      for (const [i, title] of result.games.entries()) {
        setTimeout(() => addFeedItem(taskId, title), i * 90);
      }
      // refresh the persistent "N games, last synced ..." line so it reflects
      // this run immediately instead of only on the next page load
      await loadCredentials();
    } catch (err) {
      errorTask(taskId, err instanceof Error ? err.message : "Sync failed");
    } finally {
      librarySyncing[provider] = false;
    }
  }

  // only claims "synced" once a library sync has actually run, a game
  // count alone (e.g. from manually-added Steam games) doesn't mean that
  function formatLastSynced(
    status: ProviderCredentialStatus | undefined,
  ): string | null {
    if (!status?.last_synced_at) return null;
    const when = new Date(status.last_synced_at * 1000).toLocaleString(
      undefined,
      {
        month: "short",
        day: "numeric",
        hour: "numeric",
        minute: "2-digit",
      },
    );
    const count = status.library_games ?? 0;
    return `${count} game${count === 1 ? "" : "s"} · synced ${when}`;
  }

  return {
    credentialStatus,
    credentialsLoading,
    loadError,
    loadCredentials,
    fieldValues,
    cardSaving,
    cardError,
    cardInfo,
    passwordPlaceholder,
    saveConnection,
    clearMessages,
    clearConnection,
    psnStatus,
    psnLoading,
    npssoToken,
    psnConnecting,
    psnError,
    loadPsn,
    handleConnectPsn,
    handleDisconnectPsn,
    librarySyncing,
    handleSyncLibrary,
    formatLastSynced,
  };
}

export type ConnectionsState = ReturnType<typeof useConnections>;
