// @vitest-environment happy-dom
import { flushPromises, shallowMount } from "@vue/test-utils";
import { beforeEach, expect, it, vi } from "vitest";
import PluginManagerSection from "../components/settings/PluginManagerSection.vue";
import * as plugins from "../services/plugins";
import * as permissions from "../services/pluginPermissions";
import * as ui from "../services/pluginUi";

vi.mock("vue-router", () => ({ useRoute: () => ({ query: {} }) }));
vi.mock("../services/plugins");
vi.mock("../services/pluginPermissions");
vi.mock("../services/pluginUi");
vi.mock("../state/pluginExtensions", () => ({ refreshPluginExtensions: vi.fn() }));

beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(plugins.fetchPlugins).mockResolvedValue([]);
  vi.mocked(plugins.fetchPluginCatalogues).mockResolvedValue([]);
  vi.mocked(plugins.fetchManagerSettings).mockResolvedValue({
    automatic_updates: false, retained_versions: 1,
  } as plugins.ManagerSettings);
  vi.mocked(plugins.fetchRuntimeCapabilities).mockResolvedValue({ available: true } as plugins.RuntimeCapabilities);
  vi.mocked(plugins.fetchPluginLogs).mockResolvedValue({} as plugins.PluginDiagnostics);
  vi.mocked(plugins.fetchPluginDetails).mockResolvedValue({});
  vi.mocked(permissions.fetchPluginPermissionGrants).mockResolvedValue([]);
  vi.mocked(permissions.fetchPluginPermissionRequests).mockResolvedValue([]);
  vi.mocked(ui.fetchPluginUi).mockResolvedValue({ settings: [], pages: [], actions: [] } as unknown as ui.PluginUiDocument);
});

function plugin(id: string) {
  return { plugin_id: id, name: id, status: "running", enabled: true, version: "1",
    permissions: [], compatible: true, effective_capabilities: [], tags: [],
  } as unknown as plugins.PluginSummary;
}

it("reports a failed staged preview and allows it to be retried", async () => {
  vi.mocked(plugins.previewStagedUpdate).mockRejectedValue(new Error("Staged preview unavailable"));
  const wrapper = shallowMount(PluginManagerSection);
  await flushPromises();
  const vm = wrapper.vm as unknown as {
    reviewAvailableUpdate: (item: plugins.PluginSummary) => Promise<void>;
    error: string; previewing: boolean;
  };
  const staged = { ...plugin("staged"), staged_update: { version: "2" } } as plugins.PluginSummary;
  await expect(vm.reviewAvailableUpdate(staged)).resolves.toBeUndefined();
  expect(vm.error).toContain("Staged preview unavailable");
  expect(vm.previewing).toBe(false);
  wrapper.unmount();
});

it("ignores a failed old popup load after another plugin opens", async () => {
  let reject!: (error: Error) => void;
  vi.mocked(permissions.fetchPluginPermissionGrants).mockReturnValueOnce(new Promise((_resolve, fail) => { reject = fail; }));
  const wrapper = shallowMount(PluginManagerSection);
  await flushPromises();
  const vm = wrapper.vm as unknown as {
    openPlugin: (item: plugins.PluginSummary) => Promise<void>;
    selected: plugins.PluginSummary; error: string;
  };
  const first = vm.openPlugin(plugin("first"));
  await flushPromises();
  await vm.openPlugin(plugin("second"));
  reject(new Error("Old plugin failed"));
  await first;
  expect(vm.selected.plugin_id).toBe("second");
  expect(vm.error).toBe("");
  wrapper.unmount();
});
