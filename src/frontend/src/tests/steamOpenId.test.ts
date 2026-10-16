// @vitest-environment happy-dom
import { flushPromises, mount, type VueWrapper } from "@vue/test-utils";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("../services/settings", () => ({
  fetchProviderCredentials: vi.fn().mockResolvedValue({}),
  saveProviderCredentials: vi.fn(),
  deleteProviderCredentials: vi.fn(),
}));
vi.mock("../services/psn", () => ({
  fetchPsnStatus: vi.fn().mockResolvedValue({ connected: false }),
  connectPsn: vi.fn(),
  disconnectPsn: vi.fn(),
}));
vi.mock("../services/librarySync", () => ({ syncLibrary: vi.fn() }));

import ConnectionManageDialog from "../components/settings/ConnectionManageDialog.vue";
import {
  CONNECTION_BY_KEY,
  steamOpenIdOutcome,
  useConnections,
} from "../composables/useConnections";

let wrapper: VueWrapper | undefined;
afterEach(() => {
  wrapper?.unmount();
  wrapper = undefined;
});

function openDialog(key: string) {
  const state = useConnections();
  wrapper = mount(ConnectionManageDialog, {
    props: { connection: CONNECTION_BY_KEY[key], state },
    attachTo: document.body,
  });
  return state;
}

describe("Steam OpenID verification", () => {
  it("describes each outcome the callback reports", () => {
    expect(steamOpenIdOutcome("verified")).toEqual({
      message:
        "Steam confirmed your profile. Add your Web API key to import your public library.",
      error: false,
    });
    for (const outcome of ["cancelled", "expired", "conflict", "failed"])
      expect(steamOpenIdOutcome(outcome)?.error).toBe(true);
    expect(steamOpenIdOutcome("anything-else")).toBeNull();
    expect(steamOpenIdOutcome(["verified"])).toBeNull();
    expect(steamOpenIdOutcome(undefined)).toBeNull();
  });

  it("offers Steam's own sign-in page for the legacy profile only", async () => {
    openDialog("Steam");
    await flushPromises();
    const link = document.body.querySelector<HTMLAnchorElement>(".openid a");
    expect(link?.getAttribute("href")).toBe("/api/settings/steam/openid/start");
    expect(link?.textContent).toContain("Verify profile with Steam");
    wrapper?.unmount();
    openDialog("RetroAchievements");
    await flushPromises();
    expect(document.body.querySelector(".openid")).toBeNull();
  });

  it("opens the legacy section to show the verification result", async () => {
    const state = openDialog("Steam");
    await flushPromises();
    const section = () =>
      document.body.querySelector<HTMLDetailsElement>(
        "details.credential-fields",
      );
    expect(section()?.open).toBe(false);
    state.cardInfo.Steam = steamOpenIdOutcome("verified")!.message;
    await flushPromises();
    expect(section()?.open).toBe(true);
    expect(section()?.textContent).toContain("Steam confirmed your profile.");
  });
});
