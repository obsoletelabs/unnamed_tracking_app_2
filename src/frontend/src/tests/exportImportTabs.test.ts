// @vitest-environment happy-dom
import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import ExportImportSection from "../components/settings/ExportImportSection.vue";
import {
  fetchBackupStatus,
  fetchLibraryExport,
  previewMal,
} from "../services/exportImport";
import {
  EXPORT_SETTINGS_TABS,
  buildSettingsSearchIndex,
  searchSettings,
} from "../state/settingsRegistry";
import type { SettingsGroup } from "../components/settings/SettingsNav.vue";

vi.mock("../services/exportImport", async (original) => ({
  ...(await original<typeof import("../services/exportImport")>()),
  fetchBackupStatus: vi.fn(),
  fetchLibraryExport: vi.fn(),
  previewMal: vi.fn(),
}));
vi.mock("../services/preferences", async (original) => ({
  ...(await original<typeof import("../services/preferences")>()),
  fetchPreferences: vi.fn(async () => ({})),
}));
beforeEach(() => {
  vi.mocked(fetchBackupStatus).mockResolvedValue({
    enabled: true,
    interval_hours: 24,
    backups_kept: 7,
    backup_count: 3,
    last_backup_at: 1_800_000_000,
  } as never);
});
afterEach(() => vi.clearAllMocks());

const tabs = (w: ReturnType<typeof mount>) => w.findAll('[role="tab"]');
const headings = (w: ReturnType<typeof mount>) =>
  w.findAll("h3").map((h) => h.text());

it("splits the page into export, import, restore and backups, each reachable by keyboard (#259)", async () => {
  const wrapper = mount(ExportImportSection);
  await flushPromises();
  expect(tabs(wrapper).map((t) => t.text())).toEqual(
    EXPORT_SETTINGS_TABS.map((t) => t.label),
  );
  expect(headings(wrapper)).toEqual(["Export", "Export as a spreadsheet"]);
  expect(wrapper.text()).not.toContain("AniList Import");

  await tabs(wrapper)[1].trigger("click");
  await flushPromises();
  expect(headings(wrapper)[0]).toBe("Import from another site");
  // the AniList import now lives here, loaded when this tab is first opened
  await vi.waitFor(() => expect(wrapper.text()).toContain("AniList Import"));

  await tabs(wrapper)[2].trigger("click");
  expect(headings(wrapper)).toEqual([
    "Restore movies, TV shows and anime",
    "Import games",
  ]);

  await tabs(wrapper)[3].trigger("click");
  expect(headings(wrapper)).toEqual([
    "Library export or full backup?",
    "Automatic backups",
  ]);
  expect(wrapper.text()).not.toContain("AniList Import");
  const text = wrapper.text();
  expect(text).toContain("not for disaster recovery");
  expect(text).toContain("Last backup");
  expect(wrapper.find('[aria-selected="true"]').text()).toBe("Backups");
});

it("keeps every action reachable: nothing that existed was dropped", async () => {
  const wrapper = mount(ExportImportSection);
  await flushPromises();
  const seen = new Set<string>();
  for (const tab of tabs(wrapper)) {
    await tab.trigger("click");
    wrapper
      .findAll("button, label.upload-label")
      .forEach((el) => seen.add(el.text()));
  }
  for (const action of ["Export library", "Export CSV"])
    expect([...seen].some((text) => text.includes(action))).toBe(true);
  // the three file pickers: another site, restore, games
  await tabs(wrapper)[1].trigger("click");
  expect(wrapper.findAll("input[type=file]")).toHaveLength(1);
  await tabs(wrapper)[2].trigger("click");
  expect(wrapper.findAll("input[type=file]")).toHaveLength(2);
});

it("opens the tab a link or a search result asks for", async () => {
  const restore = mount(ExportImportSection, {
    props: { initialTab: "restore" },
  });
  await flushPromises();
  expect(headings(restore)[0]).toBe("Restore movies, TV shows and anime");
  const unknown = mount(ExportImportSection, { props: { initialTab: "nope" } });
  await flushPromises();
  expect(headings(unknown)[0]).toBe("Export");
  const groups = [
    {
      area: "preferences",
      label: "Preferences",
      sections: [{ id: "export", label: "Export / Import" }],
    },
  ] as SettingsGroup[];
  const index = buildSettingsSearchIndex(groups);
  expect(searchSettings(index, "letterboxd")[0]).toMatchObject({
    section: "export",
    tab: "import",
  });
  expect(searchSettings(index, "disaster")[0]).toMatchObject({
    section: "export",
    tab: "backups",
  });
});

it("keeps a preview and its busy and error state while you look at another tab", async () => {
  vi.mocked(previewMal).mockResolvedValue({
    total: 2,
    new_count: 1,
    existing: [],
    identical: 1,
  } as never);
  const wrapper = mount(ExportImportSection, {
    props: { initialTab: "import" },
  });
  await flushPromises();
  const input = wrapper.get("input[type=file]");
  const file = new File(["<xml/>"], "list.xml", { type: "text/xml" });
  Object.defineProperty(input.element, "files", { value: [file] });
  await input.trigger("change");
  await flushPromises();
  expect(wrapper.text()).toContain("2 titles in the file");
  await tabs(wrapper)[0].trigger("click");
  await tabs(wrapper)[1].trigger("click");
  expect(wrapper.text()).toContain("2 titles in the file");
  expect(fetchLibraryExport).not.toHaveBeenCalled();
});
