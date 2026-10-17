// @vitest-environment happy-dom
import { mount, type VueWrapper } from "@vue/test-utils";
import { afterEach, describe, expect, it } from "vitest";
import TagFilterPicker from "../components/TagFilterPicker.vue";
import { tagFilterOptions } from "../utils/genres";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const LIBRARY = [
  ["Racing", "Steam: race"],
  ["Race", "Roguelite"],
  ["Roguelike", "Sci-fi"],
  ["Science Fiction", "Hack & Slash"],
];

// Vitest runs from the frontend package root.
const readSource = (path: string) =>
  readFileSync(resolve(process.cwd(), "src", path), "utf8");

let wrapper: VueWrapper | undefined;
afterEach(() => {
  wrapper?.unmount();
  wrapper = undefined;
});

function picker(
  included: string[] = [],
  excluded: string[] = [],
  tagLists = LIBRARY,
) {
  wrapper = mount(TagFilterPicker, {
    props: { options: tagFilterOptions(tagLists), included, excluded },
    attachTo: document.body,
  });
  return wrapper;
}

function names(view: VueWrapper) {
  return view
    .findAll('[role="option"] .tag-picker-option-name')
    .map((option) => option.text());
}

describe("TagFilterPicker", () => {
  it("renders a constant number of controls however many tags the library has", async () => {
    const steamLibrary = Array.from({ length: 120 }, (_, index) => [
      `Player tag ${index}`,
      `Another tag ${index}`,
    ]);
    const view = picker([], [], steamLibrary);
    // closed: one search box and the two mode buttons, no chip per tag
    expect(view.findAll("button")).toHaveLength(2);
    expect(view.findAll('[role="option"]')).toHaveLength(0);
    const input = view.get('[role="combobox"]');
    expect(input.attributes("placeholder")).toBe("Tags & genres (240)");
    await input.trigger("focus");
    expect(view.findAll('[role="option"]')).toHaveLength(240);
    await input.setValue("tag 29");
    const found = names(view);
    expect(found).toHaveLength(2);
    expect(found).toEqual(["Another tag 29", "Player tag 29"]);
    await input.setValue("tag 1");
    // names that start with the search come before names that only contain it
    await input.setValue("another tag 1");
    expect(names(view)[0]).toBe("Another tag 1");
  });

  it("finds a merged choice by any raw spelling and shows what it covers", async () => {
    const view = picker();
    const input = view.get('[role="combobox"]');
    await input.trigger("focus");
    await input.setValue("steam: race");
    expect(names(view)).toEqual(["Racing"]);
    expect(view.get(".tag-picker-option-detail").text()).toBe(
      "Also matches Race, Steam: race",
    );
    await input.setValue("science fic");
    expect(names(view)).toEqual(["Sci-fi"]);
    await input.setValue("rogue");
    // similar tags stay separate choices
    expect(names(view)).toEqual(["Roguelike", "Roguelite"]);
    await input.setValue("hack and");
    expect(view.get(".tag-picker-option-detail").text()).toBe(
      "Counts as Action",
    );
  });

  it("includes with Enter, excludes with Shift+Enter and moves with the arrows", async () => {
    const view = picker();
    const input = view.get('[role="combobox"]');
    await input.trigger("keydown", { key: "ArrowDown" });
    expect(input.attributes("aria-expanded")).toBe("true");
    const first = input.attributes("aria-activedescendant");
    expect(first).toBeTruthy();
    expect(view.get(`#${CSS.escape(first!)}`).text()).toContain("Action");
    await input.trigger("keydown", { key: "ArrowDown" });
    await input.trigger("keydown", { key: "Enter" });
    await input.trigger("keydown", { key: "ArrowUp" });
    await input.trigger("keydown", { key: "Enter", shiftKey: true });
    // ArrowUp from the first row wraps to the last
    await input.trigger("keydown", { key: "ArrowUp" });
    const last = input.attributes("aria-activedescendant")!;
    expect(view.get(`#${CSS.escape(last)}`).text()).toContain("Roguelite");
    expect(view.emitted("toggle")).toEqual([
      ["Racing", "include"],
      ["Action", "exclude"],
    ]);
    expect(view.get('[aria-live="polite"]').text()).toBe("Excluded Action");
    await input.trigger("keydown", { key: "Escape" });
    expect(input.attributes("aria-expanded")).toBe("false");
    expect(view.find('[role="listbox"]').exists()).toBe(false);
  });

  it("applies the selected mode to clicks and Enter", async () => {
    const view = picker();
    await view.get('button[aria-pressed="false"]').trigger("click");
    expect(view.get('button[aria-pressed="true"]').text()).toBe("Exclude");
    const input = view.get('[role="combobox"]');
    await input.trigger("focus");
    await input.setValue("sci");
    await view.get('[role="option"]').trigger("click");
    await input.trigger("keydown", { key: "Enter" });
    await input.trigger("keydown", { key: "Enter", shiftKey: true });
    expect(view.emitted("toggle")).toEqual([
      ["Sci-fi", "exclude"],
      ["Sci-fi", "exclude"],
      ["Sci-fi", "include"],
    ]);
  });

  it("marks chosen rows and removes chosen chips by mode", async () => {
    const view = picker(["Racing"], ["Steam: race", "Roguelite"]);
    const input = view.get('[role="combobox"]');
    await input.trigger("focus");
    const racing = view
      .findAll('[role="option"]')
      .find((option) => option.text().includes("Racing"))!;
    // an exclusion wins, as it does when filtering
    expect(racing.attributes("aria-selected")).toBe("true");
    expect(racing.classes()).toContain("excluded");
    expect(racing.text()).toContain("Excluded");
    const chips = view.findAll(".tag-picker-chip");
    expect(chips.map((chip) => chip.attributes("aria-label"))).toEqual([
      "Remove included Racing",
      "Remove excluded Steam: race",
      "Remove excluded Roguelite",
    ]);
    await chips[2]!.trigger("click");
    await chips[0]!.trigger("click");
    expect(view.emitted("remove")).toEqual([
      ["Roguelite", "exclude"],
      ["Racing", "include"],
    ]);
  });

  it("keeps the phone filter panel to two columns and the menu bounded", () => {
    const page = readSource("styles/pages/game-library.css");
    const phone = page.slice(page.lastIndexOf("@media (max-width: 760px)"));
    expect(phone).toMatch(
      /\.advanced-panel\s*{[^}]*grid-template-columns:\s*repeat\(2,\s*minmax\(0,\s*1fr\)\)/,
    );
    const component = readSource("components/TagFilterPicker.vue");
    expect(component).toMatch(
      /\.tag-picker-menu\s*{[^}]*max-height:\s*min\(340px,\s*60vh\)/,
    );
    expect(component).toMatch(/\.tag-picker-list\s*{[^}]*overflow-y:\s*auto/);
  });
});
