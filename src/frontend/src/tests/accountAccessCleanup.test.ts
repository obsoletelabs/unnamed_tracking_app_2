// @vitest-environment happy-dom
import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, expect, it, vi } from "vitest";
import { ref } from "vue";
import ConnectedAppsSection from "../components/settings/ConnectedAppsSection.vue";
import SessionsSection from "../components/settings/SessionsSection.vue";
import type { BrowserSession } from "../services/sessions";
import type { IntegrationGrant } from "../services/delegation";

const api = vi.hoisted(() => ({
  fetchIntegrationGrants: vi.fn(),
  revokeIntegrationGrant: vi.fn(),
  reduceIntegrationGrant: vi.fn(),
  fetchSessions: vi.fn(),
  revokeSession: vi.fn(),
  revokeSessions: vi.fn(),
}));
vi.mock("../services/delegation", async (original) => ({
  ...(await original<typeof import("../services/delegation")>()),
  fetchIntegrationGrants: api.fetchIntegrationGrants,
  revokeIntegrationGrant: api.revokeIntegrationGrant,
  reduceIntegrationGrant: api.reduceIntegrationGrant,
}));
vi.mock("../services/sessions", async (original) => ({
  ...(await original<typeof import("../services/sessions")>()),
  fetchSessions: api.fetchSessions,
  revokeSession: api.revokeSession,
  revokeSessions: api.revokeSessions,
}));
vi.mock("../state/auth", () => ({
  currentUser: ref({ id: "owner" }),
  authCheckFailed: ref(false),
  checkAuth: vi.fn(),
}));
vi.mock("../state/dialog", () => ({ useConfirm: () => async () => true }));
vi.mock("vue-router", () => ({ useRouter: () => ({ replace: vi.fn() }) }));

const grant = (id: string): IntegrationGrant => ({
  id,
  name: "Playnite",
  origin: "desktop:playnite",
  kind: "desktop",
  enabled: true,
  scopes: ["games.read"],
  created_at: 1,
  expires_at: null,
  revoked_at: null,
  external_account: { provider: "playnite", subject: id, username: "My PC" },
});
const session = (
  id: string,
  state: BrowserSession["state"],
): BrowserSession => ({
  id,
  user_id: "owner",
  username: "Owner",
  ip_address: id,
  user_agent: null,
  created_at: 1,
  last_seen_at: 1,
  expires_at: 9999999999,
  revoked_at: state === "revoked" ? 2 : null,
  state,
  is_current: false,
});

beforeEach(() => vi.resetAllMocks());

it("removes a revoked connection even if the follow-up read fails", async () => {
  api.fetchIntegrationGrants
    .mockResolvedValueOnce([grant("old"), grant("new")])
    .mockRejectedValueOnce(new Error("Read failed"));
  api.revokeIntegrationGrant.mockResolvedValue({});
  const wrapper = mount(ConnectedAppsSection);
  await flushPromises();
  await wrapper
    .findAll("button")
    .find((button) => button.text() === "Revoke access")!
    .trigger("click");
  await flushPromises();
  expect(api.revokeIntegrationGrant).toHaveBeenCalledWith("old");
  expect(wrapper.findAll("article")).toHaveLength(1);
  expect(wrapper.text()).toContain("My PC");
  wrapper.unmount();
});

it("keeps a connection visible if revocation fails", async () => {
  api.fetchIntegrationGrants.mockResolvedValue([grant("old")]);
  api.revokeIntegrationGrant.mockRejectedValue(new Error("Revoke failed"));
  const wrapper = mount(ConnectedAppsSection);
  await flushPromises();
  await wrapper
    .findAll("button")
    .find((button) => button.text() === "Revoke access")!
    .trigger("click");
  await flushPromises();
  expect(wrapper.findAll("article")).toHaveLength(1);
  expect(wrapper.get('[role="alert"]').text()).toContain("Revoke failed");
  wrapper.unmount();
});

it("allows removing expired or disabled connections", async () => {
  api.fetchIntegrationGrants.mockResolvedValue([
    { ...grant("expired"), expires_at: 1 },
  ]);
  const wrapper = mount(ConnectedAppsSection);
  await flushPromises();
  const remove = wrapper
    .findAll("button")
    .find((button) => button.text() === "Remove connection");
  expect(remove).toBeDefined();
  await remove!.trigger("click");
  await flushPromises();
  expect(api.revokeIntegrationGrant).toHaveBeenCalledWith("expired");
  expect(wrapper.findAll("article")).toHaveLength(0);
  wrapper.unmount();
});

it("shows active sessions by default and removes a revoked session from that view", async () => {
  api.fetchSessions
    .mockResolvedValueOnce([
      session("active", "active"),
      session("old", "revoked"),
    ])
    .mockResolvedValueOnce([
      session("active", "revoked"),
      session("old", "revoked"),
    ]);
  const wrapper = mount(SessionsSection);
  await flushPromises();
  expect(wrapper.findAll("article")).toHaveLength(1);
  await wrapper
    .findAll("button")
    .find((button) => button.text() === "Revoke")!
    .trigger("click");
  await flushPromises();
  expect(wrapper.findAll("article")).toHaveLength(0);
  await wrapper.get("#session-state").setValue("revoked");
  expect(wrapper.findAll("article")).toHaveLength(2);
  wrapper.unmount();
});

it("keeps all session states available to administrators", async () => {
  api.fetchSessions.mockResolvedValue([
    session("active", "active"),
    session("old", "revoked"),
  ]);
  const wrapper = mount(SessionsSection, { props: { admin: true } });
  await flushPromises();
  expect(wrapper.findAll("tbody tr")).toHaveLength(2);
  wrapper.unmount();
});
