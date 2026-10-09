import { afterEach, expect, it, vi } from "vitest";
import { createRenderer, nextTick, ssrContextKey } from "vue";
import SmtpSettingsSection from "../components/settings/SmtpSettingsSection.vue";
import { currentUser } from "../state/auth";
import { sendSmtpTest } from "../services/notifications";
import {
  fetchDeploymentSettings,
  updateDeploymentSettings,
  type DeploymentSettings,
} from "../services/deploymentSettings";

vi.mock("../services/deploymentSettings", () => ({
  fetchDeploymentSettings: vi.fn(),
  updateDeploymentSettings: vi.fn(),
}));
vi.mock("../services/notifications", () => ({ sendSmtpTest: vi.fn() }));

// Exercise the real SFC's mounted state and save contract in Vitest's Node runtime.
// The browser regression separately checks the actual select/input event bindings.
const renderer = createRenderer<object, object>({
  createElement: () => ({}),
  createText: () => ({}),
  createComment: () => ({}),
  insert: () => {},
  remove: () => {},
  setText: () => {},
  setElementText: () => {},
  patchProp: () => {},
  parentNode: () => null,
  nextSibling: () => null,
});
interface SmtpState {
  values: Record<string, string>;
  changeTransport(): void;
  editField(key: string): void;
  useDefaultPort(): void;
  save(): Promise<void>;
  testAddress: string;
  testResult: string;
  testError: string;
  sendTest(): Promise<void>;
}
const applications: ReturnType<typeof renderer.createApp>[] = [];
afterEach(() => {
  applications.splice(0).forEach((app) => app.unmount());
  vi.clearAllMocks();
  currentUser.value = null;
});
function deployment(port: number | null, mode = "starttls", locked = false) {
  return {
    smtp: {
      configured: true,
      tls_mode: mode as "starttls" | "ssl" | "none",
      secure_transport: true,
    },
    providers: { smtp_port: port, smtp_tls_mode: mode },
    provider_locks: { smtp_port: locked },
    real_ip: {
      header: "",
      trusted_proxies: "",
      locked: { header: false, trusted_proxies: false },
    },
    oidc: {
      enabled: false,
      issuer_url: null,
      client_id: null,
      scopes: null,
      redirect_uri: null,
      groups_claim: null,
      admin_group: null,
      user_match_field: null,
      default_login_method: null,
      login_button_text: null,
      allow_new_users: false,
      client_secret_configured: false,
      named_providers: [],
      locked_fields: {},
    },
  } satisfies DeploymentSettings;
}
async function mount(result: DeploymentSettings): Promise<SmtpState> {
  vi.mocked(fetchDeploymentSettings).mockResolvedValue(result);
  vi.mocked(updateDeploymentSettings).mockResolvedValue(result);
  const app = renderer.createApp({
    ...SmtpSettingsSection,
    render: () => null,
  });
  app.provide(ssrContextKey, { modules: new Set() });
  applications.push(app);
  app.mount({});
  await nextTick();
  await nextTick();
  const instance = app._instance as unknown as { setupState: SmtpState };
  return instance.setupState;
}

it("follows each security default and persists automatic ports as unset", async () => {
  const state = await mount(deployment(null));
  expect(state.values.smtp_port).toBe("587");
  for (const [mode, port] of [
    ["ssl", "465"],
    ["none", "25"],
    ["starttls", "587"],
  ]) {
    state.values.smtp_tls_mode = mode!;
    state.changeTransport();
    expect(state.values.smtp_port).toBe(port);
  }
  await state.save();
  expect(updateDeploymentSettings).toHaveBeenCalledWith({
    smtp_tls_mode: "starttls",
  });
});

it("preserves a saved custom port and can explicitly restore automatic selection", async () => {
  const state = await mount(deployment(2525));
  state.values.smtp_tls_mode = "ssl";
  state.changeTransport();
  expect(state.values.smtp_port).toBe("2525");
  state.useDefaultPort();
  expect(state.values.smtp_port).toBe("465");
  await state.save();
  expect(updateDeploymentSettings).toHaveBeenCalledWith({
    smtp_port: null,
    smtp_tls_mode: "ssl",
  });
});

it("keeps manually edited ports even if the edit equals an earlier default", async () => {
  const state = await mount(deployment(null));
  state.values.smtp_port = "587";
  state.editField("smtp_port");
  state.values.smtp_tls_mode = "ssl";
  state.changeTransport();
  expect(state.values.smtp_port).toBe("587");
  await state.save();
  expect(updateDeploymentSettings).toHaveBeenCalledWith({
    smtp_port: 587,
    smtp_tls_mode: "ssl",
  });
});

it("does not replace or submit a deployment-owned port", async () => {
  const state = await mount(deployment(null, "starttls", true));
  state.values.smtp_tls_mode = "ssl";
  state.changeTransport();
  expect(state.values.smtp_port).toBe("");
  await state.save();
  expect(updateDeploymentSettings).toHaveBeenCalledWith({
    smtp_tls_mode: "ssl",
  });
});

it("loads the selected transport default when no port was saved", async () => {
  const state = await mount(deployment(null, "none"));
  expect(state.values.smtp_port).toBe("25");
});

it("defaults test mail to the account address and tests saved settings only", async () => {
  currentUser.value = {
    id: "owner",
    username: "owner",
    email: "account@example.test",
    is_admin: true,
    steamgriddb_api_key: null,
  };
  vi.mocked(sendSmtpTest).mockResolvedValue();
  const state = await mount(deployment(null));
  expect(state.testAddress).toBe("account@example.test");
  state.values.smtp_tls_mode = "ssl";
  state.changeTransport();
  await state.sendTest();
  expect(sendSmtpTest).not.toHaveBeenCalled();
  await state.save();
  state.testAddress = "entered@example.test";
  await state.sendTest();
  expect(sendSmtpTest).toHaveBeenCalledWith("entered@example.test");
  expect(state.testResult).toContain("SMTP accepted");
  vi.mocked(sendSmtpTest).mockRejectedValue(
    new Error("SMTP test failed (smtp_rejected)"),
  );
  await state.sendTest();
  expect(state.testResult).toBe("");
  expect(state.testError).toContain("smtp_rejected");
});
