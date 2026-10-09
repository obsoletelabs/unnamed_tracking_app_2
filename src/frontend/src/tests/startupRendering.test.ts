import { describe, expect, it } from "vitest";
import * as Vue from "vue";
import { createSSRApp, defineComponent, h } from "vue";
import { renderToString } from "vue/server-renderer";
import { compileTemplate, parse } from "vue/compiler-sfc";
import appSource from "../App.vue?raw";

// Render the real shell template without unrelated timers, preferences or
// plugin requests. RouterView marks whether a destination would be mounted.
const { descriptor } = parse(appSource);
const { code } = compileTemplate({
  source: descriptor.template!.content,
  filename: "App.vue",
  id: "startup-rendering-test",
  compilerOptions: { mode: "function" },
});
const Shell = defineComponent({
  props: ["startupState", "authChecked", "currentUser", "route"],
  setup: () => ({
    sidebarShown: false,
    navigationViewport: "desktop",
    contentStyle: {},
    sidebarResizing: false,
    KEPT_ALIVE: [],
    preferencesLoaded: false,
    preferencesError: null,
    startupError: null,
  }),
  render: new Function("Vue", code)(Vue),
});
async function render(
  state: string,
  name: string,
  checked = true,
  signedIn = false,
) {
  const app = createSSRApp(Shell, {
    startupState: state,
    authChecked: checked,
    currentUser: signedIn ? { id: "member" } : null,
    route: { name, path: name === "home" ? "/" : `/${name}`, matched: [{}] },
  });
  for (const component of [
    "PwaStatus",
    "SidebarNav",
    "AppIcon",
    "AccountChip",
    "PluginExtensionSlot",
    "PluginOverlayHost",
    "TaskProgressToast",
    "AppDialog",
    "ShortcutsHelp",
    "ShortcutConflictNotice",
    "CommandPalette",
    "QuickTour",
    "AppearanceWelcome",
  ])
    app.component(component, { render: () => null });
  app.component(
    "RouterView",
    defineComponent({
      setup:
        (_, { slots }) =>
        () =>
          slots.default?.({
            Component: defineComponent({
              render: () => h("p", `Destination: ${name}`),
            }),
          }),
    }),
  );
  return renderToString(app);
}

describe("startup shell rendering", () => {
  it.each(["checking", "auth-required", "setup-required"])(
    "keeps Home unmounted while startup is %s, even after auth finishes",
    async (state) => {
      const html = await render(state, "home");
      expect(html).toContain("Loading…");
      expect(html).not.toContain("Destination:");
    },
  );
  it("waits for setup confirmation before displaying setup", async () => {
    expect(await render("checking", "setup", false)).toContain("Loading…");
    expect(await render("setup-required", "setup", false)).toContain(
      "Destination: setup",
    );
  });
  it.each(["login", "local-login", "oidc-start", "oidc-provider-start"])(
    "renders the confirmed public entrypoint %s without a signed-in account",
    async (name) => {
      expect(await render("auth-required", name, false)).toContain(
        `Destination: ${name}`,
      );
    },
  );
  it("renders Home only after startup confirms a signed-in account", async () => {
    expect(await render("ready", "home", true, true)).toContain(
      "Destination: home",
    );
    expect(await render("ready", "home")).not.toContain("Destination:");
  });
  it("shows connection failure instead of a stale public route", async () => {
    const html = await render("unavailable", "oidc-start", false);
    expect(html).toContain("Backend unavailable");
    expect(html).not.toContain("Destination:");
  });
});
