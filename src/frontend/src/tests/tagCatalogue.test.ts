// @vitest-environment happy-dom
import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";
import TagCatalogueSection from "../components/settings/TagCatalogueSection.vue";
import { currentUser } from "../state/auth";
import * as api from "../services/tagCatalogue";
import type { CatalogueTag, TagCatalogue } from "../services/tagCatalogue";

vi.mock("../services/tagCatalogue", async (original) => ({
  ...(await original<typeof import("../services/tagCatalogue")>()),
  fetchTagCatalogue: vi.fn(),
  fetchTagUsage: vi.fn(),
  fetchAliasSuggestions: vi.fn(),
  loadCatalogueRules: vi.fn(async () => {}),
  createTag: vi.fn(),
  renameTag: vi.fn(),
  deleteTag: vi.fn(),
  addTagAlias: vi.fn(),
  removeTagAlias: vi.fn(),
  discoverTags: vi.fn(),
  setTagSuppressed: vi.fn(),
  createDefaultTag: vi.fn(),
}));

const tag = (over: Partial<CatalogueTag> = {}): CatalogueTag => ({
  id: "t1",
  label: "Roguelike",
  key: "roguelike",
  kind: "user",
  aliases: [{ id: "a1", label: "Roguelite" }],
  ...over,
});

function catalogue(over: Partial<TagCatalogue> = {}): TagCatalogue {
  return { tags: [tag()], defaults: [], suppressed: [], ...over };
}

async function mountSection(data: TagCatalogue = catalogue()) {
  vi.mocked(api.fetchTagCatalogue).mockResolvedValue(data);
  const wrapper = mount(TagCatalogueSection);
  await flushPromises();
  return wrapper;
}

describe("TagCatalogueSection", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    currentUser.value = null;
    vi.mocked(api.fetchTagUsage).mockResolvedValue([
      { tag: "Roguelike", uses: 4, label: "Roguelike" },
      { tag: "Roguelite", uses: 2, label: "Roguelike" },
    ]);
    vi.mocked(api.fetchAliasSuggestions).mockResolvedValue([]);
  });

  it("lists a tag with its spellings and how many it includes", async () => {
    const wrapper = await mountSection();
    const card = wrapper.get('[data-testid="your-tags"]');
    expect(card.text()).toContain("Includes 2 spellings");
    expect(card.text()).toContain("Roguelite");
    expect(wrapper.get('[data-testid="in-use"]').text()).toContain(
      "shown as Roguelike",
    );
  });

  it("renames without touching aliases", async () => {
    const wrapper = await mountSection();
    vi.mocked(api.renameTag).mockResolvedValue(tag({ label: "Rogue-like" }));
    const input = wrapper.get('input[aria-label="Name for Roguelike"]');
    await input.setValue("Rogue-like");
    await wrapper
      .findAll("button")
      .find((b) => b.text() === "Rename")!
      .trigger("click");
    expect(api.renameTag).toHaveBeenCalledWith("t1", "Rogue-like");
  });

  it("asks before removing a tag and says stored tags stay put", async () => {
    const confirm = vi.fn(() => false);
    vi.stubGlobal("confirm", confirm);
    const wrapper = await mountSection();
    await wrapper
      .findAll("button")
      .find((b) => b.text() === "Remove")!
      .trigger("click");
    expect(confirm).toHaveBeenCalledWith(
      expect.stringContaining("are not changed"),
    );
    expect(api.deleteTag).not.toHaveBeenCalled();
  });

  it("applies a suggestion only when it is chosen", async () => {
    vi.mocked(api.fetchAliasSuggestions).mockResolvedValue([
      { tag: "Roguelike", alias: "Roguelite", uses: [4, 2] },
    ]);
    vi.mocked(api.createTag).mockResolvedValue(tag());
    const wrapper = await mountSection(catalogue({ tags: [] }));
    expect(api.addTagAlias).not.toHaveBeenCalled();
    await wrapper.get('[data-testid="suggestions"] button').trigger("click");
    await flushPromises();
    expect(api.createTag).toHaveBeenCalledWith("Roguelike");
    expect(api.addTagAlias).toHaveBeenCalledWith("t1", "Roguelite");
  });

  it("lets an ordinary user show an original spelling but not edit defaults", async () => {
    const wrapper = await mountSection(
      catalogue({
        tags: [],
        defaults: [
          tag({
            id: "d1",
            label: "RPG",
            aliases: [{ id: "da", label: "JRPG" }],
          }),
        ],
      }),
    );
    const defaults = wrapper.get('[data-testid="defaults"]');
    expect(defaults.find('input[aria-label^="Default name"]').exists()).toBe(
      false,
    );
    await defaults.get("button.text").trigger("click");
    expect(api.setTagSuppressed).toHaveBeenCalledWith("JRPG", true);
  });

  it("offers default management to administrators", async () => {
    currentUser.value = { is_admin: true } as typeof currentUser.value;
    const wrapper = await mountSection(catalogue({ tags: [], defaults: [] }));
    expect(
      wrapper.get('[data-testid="defaults"]').find("#new-default").exists(),
    ).toBe(true);
  });

  it("shows the server's explanation when a change is refused", async () => {
    const wrapper = await mountSection();
    vi.mocked(api.addTagAlias).mockRejectedValue(
      new Error("That spelling is a catalogue tag of its own."),
    );
    await wrapper
      .get('input[aria-label="Add a spelling that means Roguelike"]')
      .setValue("Beta");
    await wrapper.get(".alias-row").trigger("submit");
    await flushPromises();
    expect(wrapper.get('[role="alert"]').text()).toContain(
      "catalogue tag of its own",
    );
  });
});
