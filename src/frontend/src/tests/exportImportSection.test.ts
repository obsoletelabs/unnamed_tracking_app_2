// @vitest-environment happy-dom
import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, describe, expect, it, vi } from "vitest";
import ExportImportSection from "../components/settings/ExportImportSection.vue";
import * as api from "../services/exportImport";

vi.mock("../services/exportImport", () => ({
  fetchLibraryExport: vi.fn(),
  importLibrary: vi.fn(),
  fetchBackupStatus: vi.fn(),
  previewMal: vi.fn(),
  importMal: vi.fn(),
  previewList: vi.fn(),
  importList: vi.fn(),
  restoreMedia: vi.fn(),
  fetchMediaCsv: vi.fn(),
  importYamtrack: vi.fn(),
}));

afterEach(() => vi.clearAllMocks());

function mountAt(initialTab?: string) {
  vi.mocked(api.fetchBackupStatus).mockResolvedValue({
    enabled: true,
    interval_hours: 24,
    backups_kept: 7,
    last_backup_at: null,
    backup_count: 0,
  });
  return mount(ExportImportSection, {
    props: { initialTab },
    global: {
      stubs: { AniListImportSection: { template: "<div>AniList</div>" } },
    },
  });
}

async function pickFile(input: HTMLInputElement, file: File) {
  Object.defineProperty(input, "files", { value: [file], configurable: true });
  input.dispatchEvent(new Event("change"));
  await flushPromises();
}

describe("Export / Import workflows", () => {
  it("separates every action into Export, Import, Restore and Backups tabs", async () => {
    const wrapper = mountAt();
    await flushPromises();
    const tabs = wrapper.findAll('[role="tab"]').map((t) => t.text());
    expect(tabs).toEqual(["Export", "Import", "Restore", "Backups"]);
    expect(wrapper.text()).toContain("Export library");
    expect(wrapper.text()).toContain("Export CSV");
    expect(wrapper.text()).not.toContain("Restore games");

    await wrapper.findAll('[role="tab"]')[1]!.trigger("click");
    expect(wrapper.text()).toContain("Import from another site");
    for (const label of ["MyAnimeList", "Letterboxd", "IMDb", "Yamtrack"])
      expect(wrapper.text()).toContain(label);
    expect(wrapper.text()).toContain("AniList");
    expect(wrapper.text()).not.toContain("Export CSV");

    await wrapper.findAll('[role="tab"]')[2]!.trigger("click");
    expect(wrapper.text()).toContain("Restore games");
    expect(wrapper.text()).toContain("Restore movies, TV shows and anime");

    await wrapper.findAll('[role="tab"]')[3]!.trigger("click");
    expect(wrapper.text()).toContain("Automatic backups");
    expect(wrapper.text()).toContain("PostgreSQL database");
  });

  it("restores games with identity, manual achievements and completion intact", async () => {
    vi.mocked(api.importLibrary).mockResolvedValue({
      created: 1,
      skipped: 1,
      errors: ["Broken entry"],
      manual_achievements: 3,
      manual_completion: 1,
    });
    const wrapper = mountAt("restore");
    await flushPromises();
    const exported = {
      games: [{ id: "g1", title: "Game", identity: { steam_app_id: 10 } }],
      manual_achievements: [{ game_id: "g1", name: "Done" }],
      manual_completion: [{ game_id: "g1", completed: true }],
    };
    const input = wrapper.findAll('input[type="file"]')[1]!
      .element as HTMLInputElement;
    await pickFile(
      input,
      new File([JSON.stringify(exported)], "library-export.json"),
    );
    expect(api.importLibrary).toHaveBeenCalledWith(exported);
    const text = wrapper.text();
    expect(text).toContain("Imported 1 game.");
    expect(text).toContain("3 manual achievement(s) and");
    expect(text).toContain("1 completion choice(s)");
    expect(text).toContain("1 skipped.");
    expect(text).toContain("Broken entry");
  });

  it("reports media restore counts and errors inside the Restore tab only", async () => {
    vi.mocked(api.restoreMedia).mockResolvedValue({
      created: { movies: 2, tv_shows: 1, anime: 0 },
      skipped: { movies: 1, tv_shows: 0, anime: 4 },
      errors: ["Bad show"],
    });
    const wrapper = mountAt("restore");
    await flushPromises();
    const input = wrapper.findAll('input[type="file"]')[0]!
      .element as HTMLInputElement;
    const file = new File(["{}"], "library-export.json");
    await pickFile(input, file);
    expect(api.restoreMedia).toHaveBeenCalledWith(file);
    expect(wrapper.text()).toContain("movies: 2 added, 1 skipped.");
    expect(wrapper.text()).toContain("anime: 0 added, 4 skipped.");
    expect(wrapper.text()).toContain("Bad show");

    await wrapper.findAll('[role="tab"]')[0]!.trigger("click");
    expect(wrapper.text()).not.toContain("Bad show");
  });

  it("explains when the backup status cannot be loaded", async () => {
    vi.mocked(api.fetchBackupStatus).mockRejectedValue(new Error("down"));
    const failed = mount(ExportImportSection, {
      props: { initialTab: "backup" },
      global: { stubs: { AniListImportSection: true } },
    });
    await flushPromises();
    expect(failed.text()).toContain("could not be loaded");
  });
});
