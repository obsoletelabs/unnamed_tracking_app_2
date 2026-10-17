// @vitest-environment happy-dom
import { flushPromises, mount, type VueWrapper } from "@vue/test-utils";
import { afterEach, expect, it, vi } from "vitest";
import PluginUiHost from "../components/plugins/PluginUiHost.vue";
import {
  fetchPluginSettings,
  PluginActionError,
  type PluginUiDocument,
  type UiValues,
} from "../services/pluginUi";

vi.mock("../services/pluginUi", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../services/pluginUi")>()),
  fetchPluginSettings: vi.fn(),
}));

const document: PluginUiDocument = {
  schema_version: "v1",
  plugin_id: "official.password-reset",
  title: "Password Reset",
  settings: [
    {
      id: "recovery",
      title: "Account recovery",
      description: "",
      fields: [
        {
          id: "token_lifetime_seconds",
          label: "Link lifetime (seconds)",
          type: "number",
          description: "",
          required: true,
          secret: false,
          default: 1200,
          options: [],
          validation: { minimum: 600, maximum: 3600 },
        },
        {
          id: "note",
          label: "Note",
          type: "text",
          description: "",
          required: false,
          secret: false,
          default: "Default note",
          options: [],
        },
        {
          id: "api_token",
          label: "API token",
          type: "password",
          description: "",
          required: false,
          secret: true,
          options: [],
        },
      ],
    },
  ],
  actions: [{ id: "enable", label: "Enable or save recovery" }],
  tables: [],
  dialogs: [],
  menus: [],
  pages: [
    {
      id: "configure",
      title: "Password recovery",
      description: "",
      settings: ["recovery"],
      actions: ["enable"],
      tables: [],
      dialogs: [],
    },
    {
      id: "about",
      title: "About",
      description: "",
      settings: [],
      actions: [],
      tables: [],
      dialogs: [],
    },
  ],
};

let wrapper: VueWrapper | undefined;

afterEach(() => {
  wrapper?.unmount();
  wrapper = undefined;
  vi.clearAllMocks();
});

function open(source = document, pageId = "configure") {
  wrapper = mount(PluginUiHost, {
    props: { document: source, pageId },
    attachTo: globalThis.document.body,
  });
  return wrapper;
}

function input(id: string): HTMLInputElement {
  const label = [...globalThis.document.querySelectorAll("label")].find(
    (item) => item.textContent?.startsWith(id),
  );
  return globalThis.document.getElementById(
    label!.getAttribute("for")!,
  ) as HTMLInputElement;
}

function buttons(): HTMLButtonElement[] {
  return [
    ...globalThis.document.querySelectorAll<HTMLButtonElement>("form button"),
  ].filter((item) => !item.closest(".password-input"));
}

it("reloads the saved value instead of the manifest default", async () => {
  let resolve!: (values: Record<string, unknown>) => void;
  vi.mocked(fetchPluginSettings).mockReturnValueOnce(
    new Promise((done) => (resolve = done)),
  );
  open();
  await flushPromises();
  expect(fetchPluginSettings).toHaveBeenCalledWith("official.password-reset");
  // Nothing can save or act on the defaults while saved values are loading.
  expect(globalThis.document.body.textContent).toContain(
    "Loading saved settings",
  );
  expect(input("Link lifetime").disabled).toBe(true);
  expect(buttons().every((item) => item.disabled)).toBe(true);

  resolve({ token_lifetime_seconds: 900, api_token: "leaked-secret" });
  await flushPromises();
  expect(input("Link lifetime").value).toBe("900");
  expect(input("Link lifetime").disabled).toBe(false);
  expect(input("Note").value).toBe("Default note");
  expect(input("API token").value).toBe("");
  expect(buttons().every((item) => !item.disabled)).toBe(true);

  buttons()
    .find((item) => item.textContent === "Enable or save recovery")!
    .click();
  await flushPromises();
  expect(wrapper!.emitted("action")![0]![1]).toMatchObject({
    token_lifetime_seconds: 900,
    note: "Default note",
  });
  expect(wrapper!.emitted("action")![0]![1]).not.toHaveProperty("api_token");
});

it("keeps saved values across edit, save and reload cycles", async () => {
  let stored: UiValues = { token_lifetime_seconds: 900 };
  vi.mocked(fetchPluginSettings).mockImplementation(async () => ({
    ...stored,
  }));
  for (const next of [1500, 600]) {
    const host = open();
    await flushPromises();
    const lifetime = input("Link lifetime");
    expect(lifetime.value).toBe(String(stored.token_lifetime_seconds));
    lifetime.value = String(next);
    lifetime.dispatchEvent(new Event("input"));
    globalThis.document
      .querySelector("form")!
      .dispatchEvent(new Event("submit", { cancelable: true }));
    const [saved] = host.emitted("save")!.at(-1)! as [UiValues];
    expect(saved).toEqual({
      token_lifetime_seconds: next,
      note: "Default note",
    });
    expect(saved).not.toHaveProperty("api_token");
    stored = { ...stored, ...saved };
    host.unmount();
    wrapper = undefined;
  }
  open();
  await flushPromises();
  expect(input("Link lifetime").value).toBe("600");
  expect(fetchPluginSettings).toHaveBeenCalledTimes(3);
});

it("reports unreadable saved settings instead of silently showing defaults", async () => {
  vi.mocked(fetchPluginSettings).mockRejectedValueOnce(
    new PluginActionError(
      403,
      "Saved plugin settings could not be loaded (403): Permission plugin.settings has not been granted.",
    ),
  );
  open();
  await flushPromises();
  expect(globalThis.document.querySelector("[role=alert]")?.textContent).toBe(
    "Saved plugin settings could not be loaded (403): Permission plugin.settings has not been granted. Fields show the plugin defaults.",
  );
  expect(input("Link lifetime").value).toBe("1200");
  expect(input("Link lifetime").disabled).toBe(false);
});

it("loads saved values only when a page renders ordinary settings", async () => {
  vi.mocked(fetchPluginSettings).mockResolvedValue({
    token_lifetime_seconds: 900,
  });
  const host = open(document, "about");
  await flushPromises();
  expect(fetchPluginSettings).not.toHaveBeenCalled();
  await host.setProps({ pageId: "configure" });
  await flushPromises();
  expect(fetchPluginSettings).toHaveBeenCalledTimes(1);
  expect(input("Link lifetime").value).toBe("900");
  host.unmount();

  // Sandboxed frontends own their pages; the declarative form is not rendered.
  wrapper = mount(PluginUiHost, {
    props: {
      document: { ...document, frontend: { entry: "frontend/index.html" } },
      pageId: "configure",
    },
  });
  await flushPromises();
  expect(fetchPluginSettings).toHaveBeenCalledTimes(1);
});
