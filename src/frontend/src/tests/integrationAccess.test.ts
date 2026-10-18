// @vitest-environment happy-dom

import { mount, flushPromises } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";
import IntegrationAccess from "../views/IntegrationAccess.vue";
import {
  decideAccessRequest,
  fetchAccessRequest,
} from "../services/delegation";

vi.mock("vue-router", () => ({
  useRoute: () => ({ params: { requestId: "request-1" } }),
}));
vi.mock("../services/delegation", () => ({
  fetchAccessRequest: vi.fn(),
  decideAccessRequest: vi.fn(),
}));

beforeEach(() => {
  vi.mocked(fetchAccessRequest).mockResolvedValue({
    id: "request-1",
    name: "Game night",
    origin: "https://activity.example.com",
    instance_id: "instance-1",
    state: "pending",
    expires_at: 2000000000,
    maximum_grant_hours: 336,
    capabilities: [
      { scope: "games.read", description: "Read your library", write: false },
      {
        scope: "tracking.write",
        description: "Change your tracking",
        write: true,
      },
    ],
  });
  vi.mocked(decideAccessRequest).mockResolvedValue({ state: "approved" });
  vi.clearAllMocks();
});

function render() {
  return mount(IntegrationAccess, { global: { stubs: { RouterLink: true } } });
}

describe("integration consent", () => {
  it("lets the user explicitly choose access until revoked", async () => {
    const wrapper = render();
    await flushPromises();
    await wrapper.get("select").setValue("until_revoked");
    expect(wrapper.text()).toContain("Access continues until you revoke it");
    await wrapper.get("form").trigger("submit");
    await flushPromises();
    expect(decideAccessRequest).toHaveBeenCalledWith(
      "request-1",
      true,
      ["games.read"],
      null,
    );
  });
  it("shows the integration identity and leaves writes unselected", async () => {
    const wrapper = render();
    await flushPromises();
    expect(wrapper.text()).toContain("https://activity.example.com");
    const checkboxes = wrapper.findAll<HTMLInputElement>(
      'input[type="checkbox"]',
    );
    expect(checkboxes[0]?.element.checked).toBe(true);
    expect(checkboxes[1]?.element.checked).toBe(false);
    await wrapper.get("form").trigger("submit");
    await flushPromises();
    expect(decideAccessRequest).toHaveBeenCalledWith(
      "request-1",
      true,
      ["games.read"],
      24,
    );
  });

  it("requires the user's selection before approving writes", async () => {
    const wrapper = render();
    await flushPromises();
    await wrapper.findAll('input[type="checkbox"]')[1]?.setValue(true);
    await wrapper.get("form").trigger("submit");
    await flushPromises();
    expect(decideAccessRequest).toHaveBeenCalledWith(
      "request-1",
      true,
      ["games.read", "tracking.write"],
      24,
    );
  });

  it("denies without approving any selected scope", async () => {
    const wrapper = render();
    await flushPromises();
    await wrapper.get("button.ui-btn-secondary").trigger("click");
    await flushPromises();
    expect(decideAccessRequest).toHaveBeenCalledWith(
      "request-1",
      false,
      [],
      24,
    );
  });

  it("names a desktop app and its device, and asks for what it needs to sync", async () => {
    vi.mocked(fetchAccessRequest).mockResolvedValue({
      id: "request-1",
      name: "Unnamed Tracking for Playnite",
      origin: "desktop:playnite",
      kind: "desktop",
      instance_id: "instance-1",
      state: "pending",
      expires_at: 2000000000,
      maximum_grant_hours: 336,
      external_account: {
        provider: "playnite",
        subject: "playnite-1234",
        username: "Gaming PC",
      },
      capabilities: [
        {
          scope: "library.sync",
          description: "Sync your library",
          write: true,
        },
        { scope: "saves.read", description: "Download saves", write: false },
      ],
    });
    vi.mocked(decideAccessRequest).mockResolvedValue({ state: "approved" });
    const wrapper = render();
    await flushPromises();
    expect(wrapper.text()).toContain("Connect Playnite");
    expect(wrapper.text()).toContain("Desktop app on Gaming PC");
    expect(wrapper.text()).not.toContain("desktop:playnite");
    await wrapper.get("form").trigger("submit");
    await flushPromises();
    expect(decideAccessRequest).toHaveBeenCalledWith(
      "request-1",
      true,
      ["library.sync", "saves.read"],
      null,
    );
    expect(wrapper.text()).toContain("return to Playnite");
  });
});
