import { afterEach, expect, it, vi } from "vitest";
import { createRenderer, nextTick, reactive, ssrContextKey } from "vue";
import NotificationRoutingSection from "../components/settings/NotificationRoutingSection.vue";
import { fetchNotificationRouting } from "../services/notifications";
import { DEFAULT_PREFERENCES } from "../services/preferences";

const route = reactive({ query: {} as Record<string, string> });
vi.mock("vue-router", () => ({ useRoute: () => route }));
vi.mock("../services/notifications", async (original) => ({
  ...(await original<typeof import("../services/notifications")>()),
  fetchNotificationRouting: vi.fn(),
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
const apps: ReturnType<typeof renderer.createApp>[] = [];
afterEach(() => {
  apps.splice(0).forEach((app) => app.unmount());
  route.query = {};
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

it("opens the destination's actual provider and focuses host controls after routing loads", async () => {
  const id = "a134038e-9a2e-4ea1-935c-68a06d631598";
  route.query = {
    notification_destination: id,
    notification_provider: "wrong.provider",
  };
  const focus = vi.fn(),
    scrollIntoView = vi.fn();
  const getElementById = vi.fn().mockReturnValue({ focus, scrollIntoView });
  vi.stubGlobal("document", { getElementById });
  vi.mocked(fetchNotificationRouting).mockResolvedValue({
    types: [],
    providers: [],
    destinations: [
      {
        id,
        provider_id: "example.own.dm",
        provider_name: "Own DM",
        kind: "private",
        context: "external",
        trust: "SECURE",
        active: true,
        enabled: true,
        available: true,
        provider_enabled: true,
        critical_supported: false,
        eligible_types: [],
        shared_configuration: false,
      },
    ],
  });
  const app = renderer.createApp(
    { ...NotificationRoutingSection, render: () => null },
    {
      prefs: DEFAULT_PREFERENCES,
      loaded: true,
    },
  );
  app.provide(ssrContextKey, { modules: new Set() });
  apps.push(app);
  app.mount({});
  await nextTick();
  await nextTick();
  await nextTick();
  const state = (
    app._instance as unknown as {
      setupState: { focusedProvider: string; focusedDestination: string };
    }
  ).setupState;
  expect(state.focusedProvider).toBe("example.own.dm");
  expect(getElementById).toHaveBeenCalledWith(`notification-destination-${id}`);
  expect(focus).toHaveBeenCalledWith({ preventScroll: true });
  route.query = { notification_destination: "malformed" };
  await nextTick();
  await nextTick();
  expect(state.focusedDestination).toBe("");
});
