import { beforeEach, expect, it, vi } from "vitest";
import type { RouterOptions } from "vue-router";

const services = vi.hoisted(() => ({
  setup: vi.fn(),
  auth: vi.fn(),
}));
vi.mock("../services/setup", () => ({ fetchSetupStatus: services.setup }));
vi.mock("../services/auth", () => ({ fetchCurrentUser: services.auth }));
vi.mock("../state/appearance", () => ({ appearanceLoaded: { value: true } }));
vi.mock("vue-router", async (importOriginal) => {
  const actual = await importOriginal<typeof import("vue-router")>();
  return {
    ...actual,
    createWebHistory: actual.createMemoryHistory,
    createRouter: (options: RouterOptions) =>
      actual.createRouter({
        ...options,
        // Keep the production routes/guards while avoiding unrelated view setup.
        routes: options.routes.map((route) =>
          route.redirect
            ? route
            : {
                path: route.path,
                name: route.name,
                meta: route.meta,
                component: { render: () => null },
              },
        ),
      }),
  };
});
beforeEach(() => {
  vi.resetModules();
  vi.stubGlobal("window", {
    scrollY: 0,
    location: { pathname: "/", search: "", hash: "" },
  });
  const storage = new Map<string, string>();
  vi.stubGlobal("sessionStorage", {
    getItem: (key: string) => storage.get(key) ?? null,
    setItem: (key: string, value: string) => storage.set(key, value),
    removeItem: (key: string) => storage.delete(key),
  });
  services.setup.mockReset().mockResolvedValue({
    setup_required: false,
    startup_ui_enabled: true,
  });
  services.auth.mockReset().mockResolvedValue(null);
});

it("redirects a signed-out base URL to login without a return query", async () => {
  const { default: router } = await import("../router");
  await router.push("/");
  expect(router.currentRoute.value.fullPath).toBe("/login");
});
it("redirects first-run setup without returning to the base URL", async () => {
  services.setup.mockResolvedValue({
    setup_required: true,
    startup_ui_enabled: true,
  });
  const { default: router } = await import("../router");
  await router.push("/");
  expect(router.currentRoute.value.fullPath).toBe("/setup");
  expect(services.auth).not.toHaveBeenCalled();
});
it.each(["/", "/login", "/login/local", "/setup?return_to=/games"])(
  "cleans an existing login return query for %s and preserves other query fields",
  async (path) => {
    const { default: router } = await import("../router");
    await router.push({
      path: "/login",
      query: { return_to: path, oidc_error: "provider_unavailable" },
    });
    expect(router.currentRoute.value.query).toEqual({
      oidc_error: "provider_unavailable",
    });
  },
);
it("cleans redundant setup return queries", async () => {
  services.setup.mockResolvedValue({
    setup_required: true,
    startup_ui_enabled: true,
  });
  const { default: router } = await import("../router");
  await router.push("/setup?return_to=/login&step=database");
  expect(router.currentRoute.value.fullPath).toBe("/setup?step=database");
});
it("preserves a real destination when a login bookmark requires setup first", async () => {
  services.setup.mockResolvedValue({
    setup_required: true,
    startup_ui_enabled: true,
  });
  const { default: router } = await import("../router");
  const target = "/games/example?tab=media#screenshots";
  await router.push({ path: "/login", query: { return_to: target } });
  expect(router.currentRoute.value.path).toBe("/setup");
  expect(router.currentRoute.value.query.return_to).toBe(target);
});
it("keeps a real destination's query and hash across sign-in", async () => {
  const { default: router } = await import("../router");
  const target = "/games/example?tab=media#screenshots";
  await router.push(target);
  expect(router.currentRoute.value.path).toBe("/login");
  expect(router.currentRoute.value.query.return_to).toBe(target);
  const { currentUser } = await import("../state/auth");
  currentUser.value = {
    id: "member",
    username: "member",
    email: "member@example.invalid",
    is_admin: false,
    steamgriddb_api_key: null,
  };
  await router.push({
    path: "/login",
    query: { return_to: target, oidc: "success" },
  });
  expect(router.currentRoute.value.fullPath).toBe(target);
});
