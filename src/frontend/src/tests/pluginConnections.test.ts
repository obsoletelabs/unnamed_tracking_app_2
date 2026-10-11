import { describe, expect, it } from "vitest";
import { derivePluginContributions } from "../state/pluginExtensions";
import { validateDocument } from "../services/pluginUi";
import type { PluginUiDocument } from "../services/pluginUi";
import type { PluginSummary } from "../services/plugins";
import { pluginConnectionStatus } from "../services/pluginConnections";

const plugin: PluginSummary = {
  api_contract_version: "1.1.8",
  plugin_id: "example.library",
  name: "Example Library",
  version: "1.0.0",
  status: "running",
  compatible: true,
  compatibility_reason: "",
  health: "healthy",
  permissions: [],
  granted_capabilities: ["frontend.connections"],
  effective_capabilities: ["frontend.connections"],
  enabled: true,
};

const document: PluginUiDocument = {
  api_contract_version: "1.1.8",
  schema_version: "v1",
  plugin_id: plugin.plugin_id,
  title: "Example Library",
  settings: [],
  actions: [{ id: "status", label: "Status" }],
  tables: [],
  dialogs: [],
  menus: [],
  pages: [
    {
      id: "library",
      title: "Library",
      description: "",
      settings: [],
      actions: ["status"],
      tables: [],
      dialogs: [],
    },
  ],
  connections: [
    {
      id: "account",
      label: "Example account",
      description: "Import a personal library.",
      page_id: "library",
      status_action_id: "status",
      order: 0,
    },
  ],
};

describe("plugin connections", () => {
  it("are derived only with the frontend.connections grant", () => {
    const granted = derivePluginContributions(plugin, document).connections;
    expect(granted).toHaveLength(1);
    expect(granted[0]).toMatchObject({
      pluginId: "example.library",
      contributionId: "account",
      pageId: "library",
      label: "Example account",
      statusAction: { id: "status" },
    });

    const withoutGrant = {
      ...plugin,
      effective_capabilities: ["frontend.settings", "frontend.navigation.main"],
    };
    expect(
      derivePluginContributions(withoutGrant, document).connections,
    ).toEqual([]);
  });

  it("are ignored when the document and installation contracts disagree", () => {
    const stale = { ...plugin, api_contract_version: "1.1.7" };
    expect(derivePluginContributions(stale, document).connections).toEqual([]);
  });

  it("must reference declared pages and status actions", () => {
    expect(validateDocument(document)).toEqual([]);
    const broken = {
      ...document,
      connections: [
        {
          ...document.connections![0],
          page_id: "missing",
          status_action_id: "missing",
        },
      ],
    };
    expect(validateDocument(broken)).toEqual(
      expect.arrayContaining([
        "Connection account references an unknown page.",
        "Connection account references an unknown status action.",
      ]),
    );
  });

  it("require API 1.1.8 or later", () => {
    expect(
      validateDocument({ ...document, api_contract_version: "1.1.7" }),
    ).toContain("Plugin connections require API v1.1.8 or later.");
    expect(
      validateDocument({ ...document, api_contract_version: "1.1.10" }),
    ).toEqual([]);
    expect(
      validateDocument({ ...document, api_contract_version: "1.2.0" }),
    ).toEqual([]);
  });
});

describe("pluginConnectionStatus", () => {
  it("accepts a boolean state and a bounded plain-text name", () => {
    expect(
      pluginConnectionStatus({ connected: true, display_name: " Ada " }),
    ).toEqual({ connected: true, display_name: "Ada" });
    expect(
      pluginConnectionStatus({ connected: true, display_name: "x".repeat(200) })
        .display_name,
    ).toHaveLength(80);
  });

  it("treats anything but literal true as not connected", () => {
    expect(pluginConnectionStatus({ connected: "yes" })).toEqual({
      connected: false,
    });
    expect(pluginConnectionStatus({ display_name: 5 })).toEqual({
      connected: false,
    });
  });

  it("reports a failed action as an error", () => {
    expect(pluginConnectionStatus({ ok: false, error: "x" })).toEqual({
      connected: false,
      error: true,
    });
  });
});
