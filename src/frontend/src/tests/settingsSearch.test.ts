// @vitest-environment happy-dom
import { afterEach, describe, expect, it, vi } from "vitest";
import { reactive } from "vue";
import { flushPromises, mount, shallowMount } from "@vue/test-utils";
import SettingsSearch from "../components/settings/SettingsSearch.vue";
import SettingsView from "../views/Settings.vue";
import { currentUser } from "../state/auth";
import {
  clearPluginExtensions,
  refreshPluginExtensions,
} from "../state/pluginExtensions";
import {
  SETTINGS_SECTION_ALIASES,
  buildSettingsGroups,
  buildSettingsSearchIndex,
  resolveSettingsSection,
  searchSettings,
  settingsGroups,
  settingsSearchIndex,
  settingsSearchLocation,
  type SettingsSearchEntry,
} from "../state/settingsRegistry";
import type { PluginSummary } from "../services/plugins";
import type { PluginUiDocument } from "../services/pluginUi";
import type { CurrentUser } from "../services/auth";

const routing = vi.hoisted(() => ({
  route: undefined as unknown as { query: Record<string, string> },
  replace: vi.fn(),
  push: vi.fn(),
}));
vi.mock("vue-router", async (original) => ({
  ...(await original<typeof import("vue-router")>()),
  useRoute: () => routing.route,
  useRouter: () => ({ replace: routing.replace, push: routing.push }),
}));

afterEach(async () => {
  // Settings sections load lazily; let them finish before the environment closes.
  await vi.dynamicImportSettled();
  clearPluginExtensions();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
  currentUser.value = null;
  document.body.innerHTML = "";
});

function signIn(isAdmin: boolean) {
  currentUser.value = {
    id: isAdmin ? "admin" : "member",
    username: isAdmin ? "admin" : "member",
    is_admin: isAdmin,
  } as CurrentUser;
}

const SECRET = "sk-live-0123456789-do-not-index";

const plugin: PluginSummary = {
  api_contract_version: "1.1.0",
  plugin_id: "example.search",
  name: "Search example",
  version: "1.0.0",
  status: "running",
  compatible: true,
  compatibility_reason: "",
  health: "healthy",
  permissions: [],
  granted_capabilities: ["frontend.navigation", "frontend.settings"],
  effective_capabilities: [
    "frontend.navigation",
    "frontend.navigation.settings",
    "frontend.settings",
  ],
  enabled: true,
};

const document_: PluginUiDocument = {
  api_contract_version: "1.1.0",
  schema_version: "v1",
  plugin_id: plugin.plugin_id,
  title: "Search example",
  settings: [
    {
      id: "connection",
      title: "Private connection",
      description: "Raw private configuration",
      fields: [
        {
          id: "token",
          label: "Hidden token field",
          type: "password",
          description: "",
          required: false,
          secret: true,
          default: SECRET,
          options: [],
        },
      ],
    },
  ],
  actions: [],
  tables: [],
  dialogs: [],
  menus: [],
  pages: [
    {
      id: "page",
      title: "Page",
      description: "",
      settings: ["connection"],
      actions: [],
      tables: [],
      dialogs: [],
    },
  ],
  navigation: [
    {
      id: "plugin-sidebar-page",
      location: "settings.sidebar",
      label: "Sidebar extension",
      page_id: "page",
      order: 2,
      visibility: { admin_only: false },
    },
  ],
  settings_sections: [
    {
      id: "plugin-preferences",
      label: "Telescope preferences",
      page_id: "page",
      order: 1,
      visibility: { admin_only: false },
    },
    {
      id: "plugin-admin",
      label: "Telescope server",
      page_id: "page",
      order: 3,
      visibility: { admin_only: true },
    },
    {
      // A plugin can never shadow a core section through search.
      id: "users",
      label: "Telescope users",
      page_id: "page",
      order: 4,
      visibility: { admin_only: false },
    },
  ],
};

function servePlugins(list: () => PluginSummary[]) {
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async (url: string) =>
        new Response(
          JSON.stringify(url === "/api/plugins" ? list() : document_),
          { status: 200 },
        ),
    ),
  );
}

const labels = (query: string) =>
  searchSettings(settingsSearchIndex.value, query, 50).map(
    (entry) => entry.label,
  );

describe("settings search registry", () => {
  const ADMIN_ONLY = [
    "users",
    "admin-sessions",
    "oidc",
    "password-policy",
    "integration-policy",
    "app-settings",
    "branding",
    "plugins",
    "themes",
    "admin-notifications",
    "server-integrations",
    "limits",
    "tasks",
    "dev-tools",
  ];

  it("never indexes administration targets for an ordinary account", () => {
    signIn(false);
    const sections = settingsSearchIndex.value.map((entry) => entry.section);
    for (const id of ADMIN_ONLY) expect(sections).not.toContain(id);
    expect(
      settingsSearchIndex.value.some(
        (entry) => entry.area === "administration",
      ),
    ).toBe(false);
    for (const query of ["smtp", "sso", "users", "plugins", "tls", "debug"])
      expect(labels(query)).toEqual([]);
  });

  it("indexes administration targets for administrators", () => {
    signIn(true);
    const sections = settingsSearchIndex.value.map((entry) => entry.section);
    for (const id of ADMIN_ONLY) expect(sections).toContain(id);
    const [smtp] = searchSettings(settingsSearchIndex.value, "smtp");
    expect(smtp).toMatchObject({
      section: "admin-notifications",
      area: "administration",
      context: "Administration · Operations",
    });
  });

  it("is built from exactly the sections the sidebar shows", () => {
    for (const admin of [false, true]) {
      signIn(admin);
      const shown = settingsGroups.value.flatMap((group) =>
        group.sections.map((section) => section.id),
      );
      const indexed = new Set(
        settingsSearchIndex.value.map((entry) => entry.section),
      );
      expect([...indexed].sort()).toEqual([...new Set(shown)].sort());
    }
  });

  it("matches section names and deliberate keywords, ranking names first", () => {
    signIn(false);
    expect(labels("appear")[0]).toBe("Appearance & interface");
    expect(labels("dark mode")).toEqual(["Appearance & interface"]);
    expect(labels("  HOTKEYS ")).toEqual(["Keyboard Shortcuts"]);
    expect(labels("metadata")[0]).toBe("Metadata");
    expect(
      searchSettings(settingsSearchIndex.value, "notifications")[0]?.context,
    ).toBe("Account");
    expect(labels("")).toEqual([]);
    expect(labels("no such setting")).toEqual([]);
  });

  it("opens merged tabs through their containing section", () => {
    signIn(false);
    const [trash] = searchSettings(settingsSearchIndex.value, "media trash");
    expect(trash).toMatchObject({ section: "library", tab: "trash" });
    expect(settingsSearchLocation(trash!)).toEqual({
      path: "/settings",
      query: { section: "library", tab: "trash" },
    });
    const [scan] = searchSettings(settingsSearchIndex.value, "scan");
    expect(scan).toMatchObject({ section: "metadata", tab: "scan" });
    expect(settingsSearchLocation(labelEntry("Library")).query).toEqual({
      section: "library",
    });
  });

  it("agrees with the existing alias resolver for every tab entry", () => {
    signIn(true);
    for (const [alias, target] of Object.entries(SETTINGS_SECTION_ALIASES)) {
      expect(resolveSettingsSection(alias)).toEqual(target);
      if (!target.tab) continue;
      const entry = settingsSearchIndex.value.find(
        (item) => item.section === target.section && item.tab === target.tab,
      );
      expect(entry, alias).toBeDefined();
    }
    expect(resolveSettingsSection("admin", "sso")).toEqual({ section: "oidc" });
    expect(resolveSettingsSection("admin")).toEqual({ section: "users" });
    expect(resolveSettingsSection("admin", "unknown")).toEqual({
      section: "users",
    });
  });

  it("drops tab entries whose containing section is unavailable", () => {
    const groups = buildSettingsGroups(false, [], []).map((group) => ({
      ...group,
      sections: group.sections.filter((section) => section.id !== "library"),
    }));
    const index = buildSettingsSearchIndex(groups);
    expect(index.some((entry) => entry.section === "library")).toBe(false);
  });
});

function labelEntry(label: string): SettingsSearchEntry {
  const entry = settingsSearchIndex.value.find((item) => item.label === label);
  if (!entry) throw new Error(`missing ${label}`);
  return entry;
}

describe("plugin settings in search", () => {
  it("follows the plugin lifecycle and never indexes plugin configuration", async () => {
    signIn(false);
    let current: PluginSummary[] = [plugin];
    servePlugins(() => current);

    await refreshPluginExtensions();
    expect(labels("telescope")).toEqual(["Telescope preferences"]);
    expect(labels("sidebar extension")).toEqual(["Sidebar extension"]);
    const [section] = searchSettings(settingsSearchIndex.value, "telescope");
    expect(section).toMatchObject({ section: "plugin-preferences" });
    // Secrets, field labels and private configuration never become searchable.
    for (const query of [SECRET, "hidden token", "private connection", "raw"])
      expect(labels(query)).toEqual([]);
    expect(JSON.stringify(settingsSearchIndex.value)).not.toContain(SECRET);

    current = [{ ...plugin, enabled: false, status: "disabled" }];
    await refreshPluginExtensions();
    expect(labels("telescope")).toEqual([]);
    expect(labels("sidebar extension")).toEqual([]);

    current = [plugin];
    await refreshPluginExtensions();
    expect(labels("telescope")).toEqual(["Telescope preferences"]);

    current = [];
    await refreshPluginExtensions();
    expect(labels("telescope")).toEqual([]);
    expect(labels("sidebar extension")).toEqual([]);
  });

  it("hides admin-only plugin sections from ordinary accounts", async () => {
    servePlugins(() => [plugin]);
    await refreshPluginExtensions();

    signIn(false);
    expect(labels("telescope server")).toEqual([]);
    signIn(true);
    expect(labels("telescope server")).toEqual(["Telescope server"]);
    expect(
      searchSettings(settingsSearchIndex.value, "telescope server")[0],
    ).toMatchObject({ area: "administration" });
    // A contribution reusing a core ID stays hidden, as in the sidebar.
    expect(labels("telescope users")).toEqual([]);
  });
});

describe("SettingsSearch keyboard behaviour", () => {
  function render() {
    signIn(false);
    const wrapper = mount(SettingsSearch, { attachTo: document.body });
    const input = wrapper.get<HTMLInputElement>("input");
    return { wrapper, input };
  }

  it("selects results with the arrow keys and Enter while keeping focus in the field", async () => {
    const { wrapper, input } = render();
    input.element.focus();
    await input.setValue("library");
    const options = wrapper.findAll('[role="option"]');
    expect(options.map((option) => option.find("span").text())).toEqual([
      "Library",
      "Library preferences",
    ]);
    expect(input.attributes("aria-expanded")).toBe("true");
    expect(input.attributes("aria-activedescendant")).toBe(
      options[0]!.attributes("id"),
    );
    expect(options[0]!.attributes("aria-selected")).toBe("true");

    await input.trigger("keydown", { key: "ArrowDown" });
    expect(input.attributes("aria-activedescendant")).toBe(
      options[1]!.attributes("id"),
    );
    await input.trigger("keydown", { key: "ArrowDown" });
    expect(input.attributes("aria-activedescendant")).toBe(
      options[0]!.attributes("id"),
    );
    await input.trigger("keydown", { key: "ArrowUp" });
    expect(input.attributes("aria-activedescendant")).toBe(
      options[1]!.attributes("id"),
    );
    expect(document.activeElement).toBe(input.element);

    await input.trigger("keydown", { key: "Enter" });
    expect(wrapper.emitted("select")).toEqual([
      [expect.objectContaining({ section: "library", tab: "preferences" })],
    ]);
    expect(input.element.value).toBe("");
    expect(wrapper.find('[role="listbox"]').exists()).toBe(false);
    wrapper.unmount();
  });

  it("announces an empty result clearly and does not select anything", async () => {
    const { wrapper, input } = render();
    input.element.focus();
    await input.setValue("zzzz");
    expect(wrapper.find('[role="listbox"]').exists()).toBe(false);
    expect(wrapper.get('[role="status"]').text()).toBe(
      "No settings match “zzzz”.",
    );
    expect(input.attributes("aria-activedescendant")).toBeUndefined();
    await input.trigger("keydown", { key: "ArrowDown" });
    await input.trigger("keydown", { key: "Enter" });
    expect(wrapper.emitted("select")).toBeUndefined();
    wrapper.unmount();
  });

  it("clears with Escape and selects with the pointer", async () => {
    const { wrapper, input } = render();
    input.element.focus();
    await input.setValue("calendar");
    await input.trigger("keydown", { key: "Escape" });
    expect(input.element.value).toBe("");
    expect(input.attributes("aria-expanded")).toBe("false");

    await input.setValue("calendar");
    await wrapper.get('[role="option"]').trigger("click");
    expect(wrapper.emitted("select")?.[0]?.[0]).toMatchObject({
      section: "calendar",
    });
    wrapper.unmount();
  });
});

describe("Settings page navigation from search", () => {
  function renderSettings(query: Record<string, string> = {}) {
    routing.route = reactive({ query: { ...query } });
    routing.replace.mockImplementation(
      async (location: { query: Record<string, string | undefined> }) => {
        routing.route.query = Object.fromEntries(
          Object.entries(location.query).filter(
            (entry): entry is [string, string] => entry[1] !== undefined,
          ),
        );
      },
    );
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("[]", { status: 200 })),
    );
    return shallowMount(SettingsView, { attachTo: document.body });
  }

  it("opens the containing tab, updates the URL and moves focus to the section", async () => {
    signIn(false);
    const view = renderSettings({ area: "account", section: "profile" });
    await flushPromises();
    const search = view.getComponent(SettingsSearch);
    search.vm.$emit(
      "select",
      searchSettings(settingsSearchIndex.value, "media trash")[0],
    );
    await flushPromises();

    expect(routing.replace).toHaveBeenCalledWith({
      query: { section: "library", tab: "trash" },
    });
    expect(view.get("library-settings-stub").attributes("initial-tab")).toBe(
      "trash",
    );
    expect(document.activeElement).toBe(view.get(".settings-card").element);

    search.vm.$emit(
      "select",
      searchSettings(settingsSearchIndex.value, "refresh media")[0],
    );
    await flushPromises();
    expect(routing.route.query).toEqual({
      section: "metadata",
      tab: "refresh",
    });
    expect(view.get("metadata-settings-stub").attributes("initial-tab")).toBe(
      "refresh",
    );

    search.vm.$emit("select", labelEntry("Calendar"));
    await flushPromises();
    // A stale tab from the previous result is never carried forward.
    expect(routing.route.query).toEqual({ section: "calendar" });
    view.unmount();
  });

  it("keeps legacy links resolving to the same current area and tab", async () => {
    signIn(false);
    const view = renderSettings({ section: "media-trash" });
    await flushPromises();
    expect(view.get("library-settings-stub").attributes("initial-tab")).toBe(
      "trash",
    );
    view.unmount();
  });

  it("shows the search in the shared layout for ordinary accounts without admin results", async () => {
    signIn(false);
    const view = renderSettings();
    await flushPromises();
    expect(view.findComponent(SettingsSearch).exists()).toBe(true);
    expect(
      searchSettings(settingsSearchIndex.value, "users").map(
        (entry) => entry.section,
      ),
    ).toEqual([]);
    view.unmount();
  });
});
