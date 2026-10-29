// @vitest-environment happy-dom
import { flushPromises, mount } from "@vue/test-utils";
import { expect, it, vi } from "vitest";
import LibrarySyncMenu from "../components/LibrarySyncMenu.vue";

const state = vi.hoisted(() => ({
  loadCredentials: vi.fn(),
  refresh: vi.fn(),
}));
vi.mock("vue-router", () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock("../composables/useConnections", () => ({
  CONNECTIONS: [],
  isConnectionConnected: () => false,
  useConnections: () => ({ loadCredentials: state.loadCredentials }),
}));
vi.mock("../state/pluginExtensions", async () => {
  const { ref } = await import("vue");
  return { pluginConnections: ref([]), refreshPluginExtensions: state.refresh };
});

it("loads connection state only when Sync/import opens", async () => {
  const wrapper = mount(LibrarySyncMenu, {
    global: { stubs: { RouterLink: true } },
  });
  try {
    await flushPromises();
    expect(state.loadCredentials).not.toHaveBeenCalled();
    expect(state.refresh).not.toHaveBeenCalled();
    const menu = wrapper.get("details");
    menu.element.open = true;
    await vi.waitFor(() =>
      expect(state.loadCredentials).toHaveBeenCalledOnce(),
    );
    expect(state.refresh).toHaveBeenCalledOnce();
    menu.element.open = false;
    await flushPromises();
    expect(state.loadCredentials).toHaveBeenCalledOnce();
  } finally {
    wrapper.unmount();
  }
});
