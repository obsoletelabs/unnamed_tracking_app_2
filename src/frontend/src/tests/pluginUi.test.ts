import { describe, expect, it } from "vitest";
import {
  approvePluginAction,
  buildInitialValues,
  validateDocument,
  validateField,
  pluginPathForPage,
  resolvePluginPageId,
  savedFieldValues,
  type PluginUiDocument,
} from "../services/pluginUi";

const document: PluginUiDocument = {
  schema_version: "v1",
  plugin_id: "example.plugin",
  title: "Example",
  settings: [
    {
      id: "general",
      title: "General",
      description: "",
      fields: [
        {
          id: "token",
          label: "Token",
          type: "password",
          description: "",
          required: true,
          secret: true,
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
      id: "settings",
      title: "Settings",
      description: "",
      settings: ["general"],
      actions: [],
      tables: [],
      dialogs: [],
    },
  ],
};

describe("plugin UI host contract", () => {
  it("requires API v1.1.11 for administration settings slots", () => {
    for (const slot of [
      "administration.users.settings",
      "administration.password-policy.after",
    ] as const) {
      const candidate = {
        ...document,
        api_contract_version: "1.1.10",
        extensions: [
          { id: "configuration", slot, page_id: "settings", order: 0 },
        ],
      };
      expect(validateDocument(candidate)).toContain(
        "Administration settings slots require API v1.1.11 or later.",
      );
      expect(
        validateDocument({ ...candidate, api_contract_version: "1.1.11" }),
      ).toEqual([]);
    }
  });
  it("rejects duplicate navigation IDs, extension IDs and route paths in either order", () => {
    const first = { ...document.pages[0]!, id: "first" };
    const second = { ...document.pages[0]!, id: "second" };
    for (const pageIds of [
      [first.id, second.id],
      [second.id, first.id],
    ]) {
      const conflicting = {
        ...document,
        pages: [first, second],
        navigation: pageIds.map((page_id) => ({
          id: "same",
          location: "main.sidebar" as const,
          label: page_id,
          page_id,
          order: 0,
          visibility: { admin_only: false },
        })),
        extensions: pageIds.map((page_id) => ({
          id: "same",
          slot: "home.after-widgets" as const,
          page_id,
          order: 0,
        })),
        routes: pageIds.map((page_id) => ({
          id: page_id,
          path: "same-path",
          page_id,
        })),
      };
      expect(validateDocument(conflicting)).toContain(
        "Navigation same is declared more than once.",
      );
      expect(validateDocument(conflicting)).toContain(
        "Extension same is declared more than once.",
      );
      expect(validateDocument(conflicting)).toContain(
        "Plugin route same-path is ambiguous.",
      );
    }
  });

  it("chooses route aliases lexically regardless of document order", () => {
    const aliases = [
      { id: "z", path: "z-alias", page_id: "settings" },
      { id: "a", path: "a-alias", page_id: "settings" },
    ];
    expect(
      pluginPathForPage({ ...document, routes: aliases }, "settings"),
    ).toBe("a-alias");
    expect(
      pluginPathForPage(
        { ...document, routes: [...aliases].reverse() },
        "settings",
      ),
    ).toBe("a-alias");
  });
  it("rejects dangling declarative references", () => {
    expect(
      validateDocument({
        ...document,
        menus: [{ id: "bad", label: "Bad", page_id: "missing" }],
      }),
    ).toHaveLength(1);
  });

  it("does not expose secret defaults", () => {
    expect(buildInitialValues(document)).toEqual({});
  });

  it("initializes fields from saved values and defaults only missing fields", () => {
    const settingsDocument: PluginUiDocument = {
      ...document,
      settings: [
        {
          ...document.settings[0],
          fields: [
            ...document.settings[0].fields,
            {
              id: "token_lifetime_seconds",
              label: "Link lifetime",
              type: "number",
              description: "",
              required: true,
              secret: false,
              default: 1200,
              options: [],
            },
            {
              id: "label",
              label: "Label",
              type: "text",
              description: "",
              required: false,
              secret: false,
              default: "Default label",
              options: [],
            },
            {
              id: "constructor",
              label: "Mode",
              type: "boolean",
              description: "",
              required: false,
              secret: false,
              options: [],
            },
          ],
        },
      ],
    };
    expect(buildInitialValues(settingsDocument)).toEqual({
      token_lifetime_seconds: 1200,
      label: "Default label",
    });
    expect(
      buildInitialValues(settingsDocument, { token_lifetime_seconds: 900 }),
    ).toEqual({ token_lifetime_seconds: 900, label: "Default label" });
    // A saved empty value is not a missing value.
    expect(
      buildInitialValues(settingsDocument, {
        token_lifetime_seconds: 900,
        label: "",
      }),
    ).toEqual({ token_lifetime_seconds: 900, label: "" });
    // Secrets, undeclared keys and values a control cannot show are ignored.
    expect(
      buildInitialValues(settingsDocument, {
        token: "leaked-secret",
        unknown: "runtime-only",
        token_lifetime_seconds: "900",
        label: { value: "object" },
      }),
    ).toEqual({ token_lifetime_seconds: 1200, label: "Default label" });
    expect(savedFieldValues(settingsDocument, {})).toEqual({});
    expect(
      savedFieldValues(settingsDocument, { constructor: true, token: "x" }),
    ).toEqual({ constructor: true });
  });

  it("validates required fields", () => {
    expect(validateField(document.settings[0].fields[0], "")).toBe(
      "This field is required.",
    );
  });

  it("requires host confirmation for destructive actions", () => {
    const action = {
      id: "revoke-session",
      label: "Revoke",
      confirmation: "Revoke this session?",
    };
    expect(approvePluginAction(action, () => false)).toBe(false);
    expect(approvePluginAction(action, () => true)).toBe(true);
    expect(
      approvePluginAction({ id: "refresh", label: "Refresh" }, () => false),
    ).toBe(true);
  });

  it("rejects incorrect select and multiselect value shapes", () => {
    const select = {
      ...document.settings[0].fields[0],
      type: "select" as const,
      secret: false,
      required: false,
      options: [{ value: "one", label: "One" }],
    };
    const multiselect = { ...select, type: "multiselect" as const };
    expect(validateField(select, ["one"])).toBe("Select one permitted option.");
    expect(validateField(multiselect, "one")).toBe(
      "Select one or more permitted options.",
    );
  });

  it("does not crash on an invalid regex rule", () => {
    const field = {
      ...document.settings[0].fields[0],
      secret: false,
      required: false,
      validation: { pattern: "[" },
    };
    expect(validateField(field, "value")).toBe(
      "Value has an invalid format rule.",
    );
  });

  it("allows only known host extension slots and declared pages", () => {
    expect(
      validateDocument({
        ...document,
        extensions: [
          {
            id: "home-summary",
            slot: "home.after-widgets",
            page_id: "settings",
            order: 0,
          },
        ],
      }),
    ).toEqual([]);

    expect(
      validateDocument({
        ...document,
        extensions: [
          {
            id: "missing-page",
            slot: "home.after-widgets",
            page_id: "missing",
            order: 0,
          },
        ],
      }),
    ).toContain("Extension missing-page references an unknown page.");
  });

  it("keeps plugin routes distinct from host Settings sections", () => {
    const routed: PluginUiDocument = {
      ...document,
      routes: [{ id: "sessions-route", path: "sessions", page_id: "settings" }],
      settings_sections: [
        {
          id: "sessions",
          label: "Sessions",
          page_id: "settings",
          order: 0,
          visibility: { admin_only: false },
        },
      ],
    };

    expect(resolvePluginPageId(routed, "sessions")).toBe("settings");
    expect(pluginPathForPage(routed, "settings")).toBe("sessions");
    expect(routed.settings_sections?.[0].id).toBe("sessions");
  });

  it("requires the current contract and a declared action for connection menu sync", () => {
    const connected: PluginUiDocument = {
      ...document,
      api_contract_version: "1.1.11",
      actions: [{ id: "sync", label: "Sync", handler: "plugin:sync" }],
      connections: [
        {
          id: "account",
          label: "Example",
          description: "",
          page_id: "settings",
          sync_action_id: "sync",
          order: 0,
        },
      ],
    };
    expect(validateDocument(connected)).toEqual([]);
    expect(
      validateDocument({ ...connected, api_contract_version: "1.1.10" }),
    ).toContain("Connection sync actions require API v1.1.11 or later.");
    expect(validateDocument({ ...connected, actions: [] })).toContain(
      "Connection account references an unknown sync action.",
    );
  });
});
