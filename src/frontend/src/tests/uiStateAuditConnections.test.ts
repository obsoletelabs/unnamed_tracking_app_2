// @vitest-environment happy-dom
import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";
import ConnectionsSection from "../components/settings/ConnectionsSection.vue";
import ConnectionManageDialog from "../components/settings/ConnectionManageDialog.vue";
import {
  CONNECTION_BY_KEY,
  useConnections,
} from "../composables/useConnections";

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
vi.mock("../services/librarySync", () => ({
  syncLibrary: vi.fn(),
  enrichSteamGames: vi.fn(),
}));
vi.mock("vue-router", () => ({
  useRoute: () => ({ query: {} }),
  useRouter: () => ({ replace: vi.fn() }),
  RouterLink: { template: "<a><slot /></a>" },
}));
vi.mock("../composables/usePluginConnections", () => ({
  usePluginConnections: () => ({
    states: {},
    loadError: { value: null },
    load: vi.fn(async () => {}),
    isSyncing: () => false,
  }),
}));

const connected = { Steam: { status: "connected" } };

beforeEach(() => vi.resetAllMocks());

describe("Connections page when the credentials cannot be loaded", () => {
  it("does not list accounts as 'Not connected', and Retry recovers", async () => {
    settings.fetchProviderCredentials
      .mockRejectedValueOnce(new Error("Credentials unavailable"))
      .mockResolvedValueOnce(connected);
    const wrapper = mount(ConnectionsSection, {
      global: {
        stubs: {
          ConnectionManageDialog: true,
          PluginConnectionDialog: true,
          SteamConnectionDialog: true,
        },
      },
    });
    await flushPromises();
    expect(wrapper.text()).toContain("Credentials unavailable");
    expect(wrapper.text()).not.toContain("Not connected");
    expect(wrapper.findAll("li.row")).toHaveLength(0);

    await wrapper
      .findAll("button")
      .find((b) => b.text() === "Retry")!
      .trigger("click");
    await flushPromises();
    expect(wrapper.text()).not.toContain("Credentials unavailable");
    expect(wrapper.text()).toContain("Steam");
    expect(wrapper.text()).toContain("Connected");
  });
});

describe("useConnections", () => {
  it("knows the credentials are loaded only after a successful read", async () => {
    settings.fetchProviderCredentials
      .mockRejectedValueOnce(new Error("Down"))
      .mockResolvedValueOnce(connected);
    const state = useConnections();
    await state.loadCredentials();
    expect(state.credentialsLoaded.value).toBe(false);
    expect(state.loadError.value).toBe("Down");
    await state.retryCredentials();
    expect(state.credentialsLoaded.value).toBe(true);
    expect(state.loadError.value).toBeNull();
    expect(state.credentialsLoading.value).toBe(false);
  });

  it("keeps an unsaved Steam edit when the credentials are read again", async () => {
    const steam = (profile: string, key = "k") => ({
      Steam: {
        status: "connected",
        fields: { steam_id: profile, api_key_hint: key },
      },
    });
    settings.fetchProviderCredentials
      .mockResolvedValueOnce(steam("111"))
      .mockResolvedValueOnce(steam("222", "k2"));
    const state = useConnections();
    await state.loadCredentials();
    expect(state.fieldValues.Steam!.steam_id).toBe("111");
    state.fieldValues.Steam!.steam_id = "typed-but-unsaved";
    await state.loadCredentials(); // e.g. after a sync
    expect(state.fieldValues.Steam!.steam_id).toBe("typed-but-unsaved");
    // a field the user left alone still follows the server
    expect(state.fieldValues.Steam!.api_key_hint).toBe("k2");
  });

  it("clears an old PlayStation error when it reloads", async () => {
    psn.fetchPsnStatus
      .mockRejectedValueOnce(new Error("PSN down"))
      .mockResolvedValueOnce({ connected: true, validated_at: null });
    const state = useConnections();
    await state.loadPsn();
    expect(state.psnLoadFailed.value).toBe(true);
    await state.loadPsn();
    expect(state.psnLoadFailed.value).toBe(false);
    expect(state.psnError.value).toBeNull();
    expect(state.psnStatus.value.connected).toBe(true);
  });
});

describe("PlayStation dialog when the status cannot be loaded", () => {
  it("shows the failure with Retry instead of a Connect form", async () => {
    psn.fetchPsnStatus
      .mockRejectedValueOnce(new Error("PSN down"))
      .mockResolvedValueOnce({ connected: true, validated_at: null });
    const state = useConnections();
    const wrapper = mount(ConnectionManageDialog, {
      props: { connection: CONNECTION_BY_KEY.PlayStation!, state },
      attachTo: document.body,
    });
    await flushPromises();
    const body = document.body.textContent ?? "";
    expect(body).toContain("PSN down");
    expect(body).not.toContain("Not connected");
    expect(document.body.querySelector("form.connection-form")).toBeNull();

    const retry = [...document.body.querySelectorAll("button")].find(
      (b) => b.textContent?.trim() === "Retry",
    )!;
    retry.click();
    await flushPromises();
    expect(document.body.textContent).not.toContain("PSN down");
    expect(document.body.textContent).toContain("Reconnect");
    wrapper.unmount();
  });
});
