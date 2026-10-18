import { beforeEach, describe, expect, it, vi } from "vitest";

const settings = vi.hoisted(() => ({
  fetchProviderCredentials: vi.fn(),
  saveProviderCredentials: vi.fn(),
  deleteProviderCredentials: vi.fn(),
}));
const psn = vi.hoisted(() => ({
  fetchPsnStatus: vi.fn(),
  connectPsn: vi.fn(),
  disconnectPsn: vi.fn(),
}));
vi.mock("../services/settings", () => settings);
vi.mock("../services/psn", () => psn);
const librarySync = vi.hoisted(() => ({ syncLibrary: vi.fn() }));
vi.mock("../services/librarySync", () => librarySync);

import { tasks } from "../state/taskProgress";
import {
  CONNECTIONS,
  CONNECTION_BY_KEY,
  isConnectionConnected,
  useConnections,
} from "../composables/useConnections";

beforeEach(() => {
  vi.clearAllMocks();
});

describe("connection catalogue", () => {
  it("lists only accounts, never metadata-only providers", () => {
    const keys = CONNECTIONS.map((connection) => connection.key);
    expect(keys).toEqual([
      "Steam",
      "PlayStation",
      "RetroAchievements",
      "Xbox",
      "GOG",
    ]);
    for (const metadataOnly of ["IGDB", "SteamGridDB", "TMDB", "OMDb"])
      expect(CONNECTION_BY_KEY[metadataOnly]).toBeUndefined();
  });

  it("treats saved and configured credentials as connected", () => {
    expect(isConnectionConnected({ status: "saved" })).toBe(true);
    expect(isConnectionConnected({ status: "configured" })).toBe(true);
    expect(isConnectionConnected({ status: "connected" })).toBe(true);
    expect(isConnectionConnected({ status: "not_configured" })).toBe(false);
    expect(isConnectionConnected({ status: "error" })).toBe(false);
    expect(isConnectionConnected(undefined)).toBe(false);
  });
});

describe("useConnections lifecycle", () => {
  it("loads status and prefills saved non-secret fields", async () => {
    settings.fetchProviderCredentials.mockResolvedValue({
      Steam: { status: "connected", fields: { steam_id: "7656" } },
    });
    const state = useConnections();
    await state.loadCredentials();
    expect(state.credentialsLoading.value).toBe(false);
    expect(state.fieldValues.Steam.steam_id).toBe("7656");
    expect(state.passwordPlaceholder("Steam", "api_key", "Web API Key")).toBe(
      "Already saved: paste a new value to replace it",
    );
  });

  it("connects with the entered fields and surfaces verification errors", async () => {
    const state = useConnections();
    state.fieldValues.Steam = { steam_id: "7656", api_key: "key" };
    settings.saveProviderCredentials.mockResolvedValueOnce({
      status: "error",
      detail: "Steam rejected this key.",
    });
    await state.saveConnection(CONNECTION_BY_KEY.Steam);
    expect(settings.saveProviderCredentials).toHaveBeenCalledWith("Steam", {
      steam_id: "7656",
      api_key: "key",
    });
    expect(state.cardError.Steam).toBe("Steam rejected this key.");

    settings.saveProviderCredentials.mockResolvedValueOnce({
      status: "connected",
    });
    await state.saveConnection(CONNECTION_BY_KEY.Steam);
    expect(state.cardError.Steam).toBeNull();
    expect(state.credentialStatus.Steam.status).toBe("connected");
  });

  it("disconnects and clears the saved form", async () => {
    const state = useConnections();
    state.fieldValues.Xbox = { client_id: "abc" };
    state.credentialStatus.Xbox = { status: "saved" };
    settings.deleteProviderCredentials.mockResolvedValue(undefined);
    await state.clearConnection(CONNECTION_BY_KEY.Xbox);
    expect(settings.deleteProviderCredentials).toHaveBeenCalledWith("Xbox");
    expect(state.credentialStatus.Xbox.status).toBe("not_configured");
    expect(state.fieldValues.Xbox).toEqual({});
  });

  it("connects and disconnects PlayStation through its token flow", async () => {
    settings.fetchProviderCredentials.mockResolvedValue({});
    const state = useConnections();
    await state.handleConnectPsn();
    expect(state.psnError.value).toBe("Paste your npsso token first.");

    state.npssoToken.value = " token ";
    psn.connectPsn.mockResolvedValue({ connected: true, validated_at: 1 });
    await state.handleConnectPsn();
    expect(psn.connectPsn).toHaveBeenCalledWith("token");
    expect(state.psnStatus.value.connected).toBe(true);
    expect(state.npssoToken.value).toBe("");

    psn.disconnectPsn.mockResolvedValue(undefined);
    await state.handleDisconnectPsn();
    expect(state.psnStatus.value.connected).toBe(false);
    // the Connections row reloads so it stops reporting a live account
    expect(settings.fetchProviderCredentials).toHaveBeenCalledTimes(2);
  });
});

describe("library sync summary", () => {
  it("names every Steam import step that did not finish", async () => {
    settings.fetchProviderCredentials.mockResolvedValue({});
    librarySync.syncLibrary.mockResolvedValue({
      games_added: 2,
      games_updated: 1,
      achievements_synced: 7,
      games: [],
      wishlist_added: 0,
      wishlist_failed: true,
      wishlist_error: "Steam rejected the API key.",
      enrich_failed: 3,
      achievements_failed: 0,
      achievements_unavailable: [],
    });
    await useConnections().handleSyncLibrary("steam");
    expect(tasks.at(-1)?.detail).toBe(
      "2 added, 1 updated, 7 achievements; wishlist not imported (Steam rejected the API key.); details or artwork missing for 3 games (use Retry details)",
    );
  });

  it("counts wishlisted games when the import succeeds", async () => {
    settings.fetchProviderCredentials.mockResolvedValue({});
    librarySync.syncLibrary.mockResolvedValue({
      games_added: 0,
      games_updated: 4,
      achievements_synced: 0,
      games: [],
      wishlist_added: 2,
    });
    await useConnections().handleSyncLibrary("steam");
    expect(tasks.at(-1)?.detail).toBe(
      "0 added, 4 updated, 2 wishlisted, 0 achievements",
    );
  });
});
