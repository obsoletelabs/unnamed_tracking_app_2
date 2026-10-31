// @vitest-environment happy-dom
import { flushPromises, mount } from "@vue/test-utils";
import { expect, it, vi } from "vitest";
import ArchiveEditDialog from "../components/ArchiveEditDialog.vue";
import MediaEditDialog from "../components/MediaEditDialog.vue";
import type { GameArchiveData } from "../services/gameArchives";
import type { MediaItem } from "../services/media";

const global = { stubs: { teleport: true } };
const media = {
  id: "shot",
  filename: "abcdef12_shot.png",
  kind: "screenshot",
  url: "/api/game/g/screenshots/screenshot/abcdef12_shot.png",
  tags: [],
  note: null,
  title: null,
  created_at: 1700000000,
} as unknown as MediaItem;
const archive: GameArchiveData = {
  id: "save",
  name: "Slot 1",
  note: null,
  tags: [],
  kind: "save",
  created_at: 1700000000,
  updated_at: 1700000000,
  versions: [],
};

function saveButton(wrapper: ReturnType<typeof mount>) {
  const button = wrapper
    .findAll("button")
    .find((b) => b.text().trim() === "Save");
  if (!button) throw new Error("no Save button");
  return button;
}

it.each([
  ["media", MediaEditDialog, { item: media }, "Boss fight"],
  ["save", ArchiveEditDialog, { archive, noun: "save" }, "Before the boss"],
] as const)(
  "keeps the %s dialog open with the typed values when the save fails",
  async (_name, component, props, typed) => {
    let accept = false;
    const onSave = vi.fn(async () => accept);
    // Both dialogs share the onSave contract under test; their other props differ.
    const wrapper = mount(component as Parameters<typeof mount>[0], {
      props: { ...props, onSave } as Record<string, unknown>,
      global,
    });
    await wrapper.find("input[type=text]").setValue(typed);
    await saveButton(wrapper).trigger("click");
    await flushPromises();
    expect(onSave).toHaveBeenCalledTimes(1);
    expect(wrapper.emitted("close")).toBeUndefined();
    expect(
      (wrapper.find("input[type=text]").element as HTMLInputElement).value,
    ).toBe(typed);
    expect(wrapper.find("[role=alert]").text()).toContain("still here");

    accept = true;
    await saveButton(wrapper).trigger("click");
    await flushPromises();
    expect(wrapper.emitted("close")).toHaveLength(1);
  },
);

it.each([
  ["media", MediaEditDialog, { item: media }],
  ["save", ArchiveEditDialog, { archive, noun: "save" }],
] as const)(
  "retains newer %s edits after an earlier save succeeds",
  async (_name, component, props) => {
    let resolve!: (value: boolean) => void;
    const slow = new Promise<boolean>((done) => {
      resolve = done;
    });
    const wrapper = mount(component as Parameters<typeof mount>[0], {
      props: { ...props, onSave: () => slow } as Record<string, unknown>,
      global,
    });
    await wrapper.find("input[type=text]").setValue("Submitted name");
    await saveButton(wrapper).trigger("click");
    await wrapper.find("input[type=text]").setValue("Newer unsaved name");
    resolve(true);
    await flushPromises();
    expect(wrapper.emitted("close")).toBeUndefined();
    expect(
      (wrapper.find("input[type=text]").element as HTMLInputElement).value,
    ).toBe("Newer unsaved name");
    wrapper.unmount();
  },
);
