import { afterEach, expect, it, vi } from "vitest";
import { createRenderer, nextTick, ssrContextKey } from "vue";
import TrustedProxyControls from "../components/settings/TrustedProxyControls.vue";
import { fetchProxyPresets, validateProxyEntries } from "../services/realIp";

vi.mock("../services/realIp", () => ({
  fetchProxyPresets: vi.fn(),
  validateProxyEntries: vi.fn(),
}));
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
interface ProxyState {
  custom: string;
  error: string | null;
  addCustom(): Promise<void>;
  remove(value: string): void;
  add(values: string[]): void;
}
const apps: ReturnType<typeof renderer.createApp>[] = [];
afterEach(() => {
  apps.splice(0).forEach((app) => app.unmount());
  vi.clearAllMocks();
});
async function mount(disabled = false) {
  vi.mocked(fetchProxyPresets).mockResolvedValue({});
  const changed = vi.fn();
  const app = renderer.createApp(
    { ...TrustedProxyControls, render: () => null },
    {
      modelValue: "127.0.0.1/32 ::1/128",
      disabled,
      "onUpdate:modelValue": changed,
    },
  );
  app.provide(ssrContextKey, { modules: new Set() });
  apps.push(app);
  app.mount({});
  await nextTick();
  return {
    state: (app._instance as unknown as { setupState: ProxyState }).setupState,
    changed,
  };
}

it("validates pasted ranges before emitting canonical entries", async () => {
  const { state, changed } = await mount();
  state.custom = "192.168.1.42/24\n::1/128";
  vi.mocked(validateProxyEntries).mockResolvedValue(
    "127.0.0.1/32 ::1/128 192.168.1.0/24",
  );
  await state.addCustom();
  expect(validateProxyEntries).toHaveBeenCalledWith(
    "127.0.0.1/32 ::1/128 192.168.1.42/24 ::1/128",
  );
  expect(changed).toHaveBeenCalledWith("127.0.0.1/32 ::1/128 192.168.1.0/24");
  expect(state.custom).toBe("");
});

it("retains rejected input without adding invalid ranges", async () => {
  const { state, changed } = await mount();
  state.custom = "999.1.1.1";
  vi.mocked(validateProxyEntries).mockRejectedValue(
    new Error("Invalid trusted proxy address or CIDR: 999.1.1.1"),
  );
  await state.addCustom();
  expect(changed).not.toHaveBeenCalled();
  expect(state.custom).toBe("999.1.1.1");
  expect(state.error).toContain("Invalid trusted proxy address");
});

it("removes only the chosen entry and deduplicates presets", async () => {
  const { state, changed } = await mount();
  state.remove("::1/128");
  expect(changed).toHaveBeenLastCalledWith("127.0.0.1/32");
  state.add(["127.0.0.1/32", "10.0.0.0/8", "10.0.0.0/8"]);
  expect(changed).toHaveBeenLastCalledWith("127.0.0.1/32 ::1/128 10.0.0.0/8");
});

it("does not modify environment-locked entries", async () => {
  const { state, changed } = await mount(true);
  state.custom = "10.0.0.0/8";
  await state.addCustom();
  state.add(["10.0.0.0/8"]);
  state.remove("::1/128");
  expect(changed).not.toHaveBeenCalled();
  expect(validateProxyEntries).not.toHaveBeenCalled();
});
