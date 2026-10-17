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

it("lists addable providers, folds unavailable ones and keeps disabled ones compact", async () => {
  vi.stubGlobal("document", { getElementById: () => null });
  const base = {
    critical_supported: false,
    configuration_scope: "user" as const,
  };
  const d = (provider_id: string, over = {}) => ({
    id: `${provider_id}-d`,
    provider_id,
    provider_name: provider_id,
    kind: "private",
    context: "external" as const,
    trust: "PRIVATE" as const,
    active: true,
    enabled: true,
    available: true,
    provider_enabled: true,
    critical_supported: false,
    eligible_types: [],
    shared_configuration: false,
    ...over,
  });
  vi.mocked(fetchNotificationRouting).mockResolvedValue({
    types: [],
    providers: [
      { ...base, id: "ok", name: "Ok", enabled: true, available: true },
      { ...base, id: "off", name: "Off", enabled: false, available: true },
      { ...base, id: "gone", name: "Gone", enabled: true, available: false },
      { ...base, id: "never", name: "Never", enabled: true, available: false },
    ],
    destinations: [d("ok"), d("off"), d("gone", { available: false })],
  });
  const app = renderer.createApp(
    { ...NotificationRoutingSection, render: () => null },
    { prefs: DEFAULT_PREFERENCES, loaded: true },
  );
  app.provide(ssrContextKey, { modules: new Set() });
  apps.push(app);
  app.mount({});
  for (let i = 0; i < 4; i++) await nextTick();
  const state = (
    app._instance as unknown as {
      setupState: {
        sortedProviders: { id: string }[];
        unavailableProviders: { id: string }[];
      };
    }
  ).setupState;
  expect(state.sortedProviders.map((p) => p.id)).toEqual(["ok", "off"]);
  expect(state.unavailableProviders.map((p) => p.id)).toEqual(["gone"]);
});
