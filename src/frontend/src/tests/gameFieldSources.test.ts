// @vitest-environment happy-dom
import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";
import GameFieldSource from "../components/game/GameFieldSource.vue";
import GameSourcesPanel from "../components/game/GameSourcesPanel.vue";
import * as fields from "../services/gameFieldSources";
import type {
  FieldSourceEntry,
  FieldSources,
} from "../services/gameFieldSources";
import * as identity from "../services/gameIdentity";
import {
  disagreeingFields,
  fieldProvenance,
  metadataSummary,
  preferenceNotInEffect,
  showsFieldSource,
} from "../utils/fieldSources";

vi.mock("../services/gameFieldSources", async (original) => ({
  ...(await original<typeof import("../services/gameFieldSources")>()),
  fetchFieldSources: vi.fn(),
  preferFieldSource: vi.fn(async () => ({ applied: true })),
  clearFieldSource: vi.fn(async () => {}),
}));
vi.mock("../services/gameIdentity", async (original) => ({
  ...(await original<typeof import("../services/gameIdentity")>()),
  fetchIdentitySources: vi.fn(),
}));

const entry = (over: Partial<FieldSourceEntry> = {}): FieldSourceEntry => ({
  origin: "metadata",
  provider: "igdb",
  locked: false,
  preferred: null,
  sources: [
    {
      sourceKey: "provider:steam",
      kind: "provider",
      label: "steam",
      value: "Supergiant",
      current: false,
    },
    {
      sourceKey: "provider:igdb",
      kind: "provider",
      label: "igdb",
      value: "Supergiant Games",
      current: true,
    },
  ],
  ...over,
});

describe("field source rules", () => {
  it("counts a field as disagreeing only when sources report different values", () => {
    const agreeing = entry();
    agreeing.sources[0].value = "Supergiant Games";
    const sources: FieldSources = {
      developer: entry(),
      publisher: agreeing,
      series: entry({ sources: [entry().sources[0]] }),
    };
    expect(disagreeingFields(sources)).toEqual(["developer"]);
  });

  it("summarises the most-used preferred source and the disputed fields", () => {
    const summary = metadataSummary({
      developer: entry({ preferred: "provider:steam" }),
      publisher: entry({ preferred: "provider:steam" }),
      description: entry({ preferred: "provider:igdb" }),
    });
    expect(summary).toMatchObject({
      preferredLabel: "steam",
      preferredFields: 2,
    });
    expect(summary.disagreeing).toEqual([
      "description",
      "developer",
      "publisher",
    ]);
  });
});

describe("provenance and preference rules", () => {
  const single = (over: Partial<FieldSourceEntry> = {}) =>
    entry({ sources: [entry().sources[1]], ...over });

  it("treats a preference as not in effect when another source supplies the value", () => {
    expect(preferenceNotInEffect(single())).toBe(false);
    expect(preferenceNotInEffect(single({ preferred: "provider:igdb" }))).toBe(
      false,
    );
    // RAWG lost the merge, so its value is not stored and cannot apply (Phase 5 D2)
    expect(preferenceNotInEffect(single({ preferred: "provider:rawg" }))).toBe(
      true,
    );
  });

  it("shows the label for a lock or an unapplied preference, not for one quiet source", () => {
    expect(showsFieldSource(undefined)).toBe(false);
    expect(showsFieldSource(single())).toBe(false);
    expect(showsFieldSource(single({ locked: true }))).toBe(true);
    expect(showsFieldSource(single({ preferred: "provider:rawg" }))).toBe(true);
    expect(showsFieldSource(entry())).toBe(true);
  });

  it("names the source of every recorded field in a fixed order", () => {
    const rows = fieldProvenance({
      tags: single(),
      developer: single({ locked: true }),
      publisher: single({ preferred: "provider:rawg" }),
      unknown_field: single(),
    });
    expect(rows.map((r) => [r.field, r.source])).toEqual([
      ["developer", "igdb"],
      ["publisher", "igdb"],
      ["tags", "igdb"],
    ]);
    expect(rows[0].locked).toBe(true);
    expect(rows[1]).toMatchObject({
      preferred: "rawg",
      preferenceNotInEffect: true,
    });
  });
});

describe("the field source label", () => {
  beforeEach(() => vi.clearAllMocks());
  const mountLabel = (e?: FieldSourceEntry) =>
    mount(GameFieldSource, {
      props: { gameId: "g1", field: "developer", entry: e },
    });

  it("shows nothing when there is a single source or none", () => {
    expect(mountLabel().find(".field-source").exists()).toBe(false);
    const single = entry({ sources: [entry().sources[0]] });
    expect(mountLabel(single).find(".field-source").exists()).toBe(false);
  });

  it("names the current source, lists every value and prefers another", async () => {
    const wrapper = mountLabel(entry());
    expect(wrapper.get(".source-chip").text()).toBe("igdb");
    expect(wrapper.find(".source-list").exists()).toBe(false);
    await wrapper.get(".source-chip").trigger("click");
    const rows = wrapper.findAll(".source-list li");
    expect(rows).toHaveLength(2);
    expect(rows[1].text()).toContain("Current");
    expect(rows[0].text()).toContain("Supergiant");
    await rows[0].get("button").trigger("click");
    await flushPromises();
    expect(fields.preferFieldSource).toHaveBeenCalledWith(
      "g1",
      "developer",
      "provider:steam",
    );
    expect(wrapper.emitted("changed")).toHaveLength(1);
  });

  it("marks the preferred source, offers to clear it and says when locked", async () => {
    const wrapper = mountLabel(
      entry({ preferred: "provider:steam", locked: true }),
    );
    await wrapper.get(".source-chip").trigger("click");
    expect(wrapper.text()).toContain("locked");
    expect(wrapper.find(".tag.preferred").text()).toBe("Preferred");
    expect(wrapper.findAll(".source-list li button")).toHaveLength(1);
    await wrapper.get(".source-list > button").trigger("click");
    await flushPromises();
    expect(fields.clearFieldSource).toHaveBeenCalledWith("g1", "developer");
  });

  it("shows a lock on the label even when every source agrees", async () => {
    const wrapper = mountLabel(
      entry({ sources: [entry().sources[1]], locked: true }),
    );
    const chip = wrapper.get(".source-chip");
    expect(chip.classes()).toContain("locked");
    expect(chip.text()).toContain("Locked");
    expect(chip.text()).toContain("igdb");
    await chip.trigger("click");
    expect(wrapper.text()).toContain("locked");
  });

  it("explains a preferred source the value does not follow", async () => {
    const wrapper = mountLabel(
      entry({ sources: [entry().sources[1]], preferred: "provider:rawg" }),
    );
    const chip = wrapper.get(".source-chip");
    expect(chip.classes()).toContain("warn");
    await chip.trigger("click");
    expect(wrapper.get(".source-list").text()).toContain(
      "You prefer rawg here, but the value shown comes from igdb",
    );
  });

  it("says when a saved preference did not change the value", async () => {
    vi.mocked(fields.preferFieldSource).mockResolvedValueOnce({
      applied: false,
    });
    const wrapper = mountLabel(entry());
    await wrapper.get(".source-chip").trigger("click");
    await wrapper.get(".source-list li button").trigger("click");
    await flushPromises();
    expect(wrapper.get("[role=status]").text()).toBe(
      "Preference saved. The value shown did not change.",
    );
    expect(wrapper.emitted("changed")).toHaveLength(1);
  });

  it("closes the list with Escape", async () => {
    const wrapper = mountLabel(entry());
    await wrapper.get(".source-chip").trigger("click");
    expect(wrapper.find(".source-list").exists()).toBe(true);
    await wrapper.get(".field-source").trigger("keydown", { key: "Escape" });
    expect(wrapper.find(".source-list").exists()).toBe(false);
  });

  it("says so and stays usable when saving fails", async () => {
    vi.mocked(fields.preferFieldSource).mockRejectedValueOnce(
      new Error("Could not save"),
    );
    const wrapper = mountLabel(entry());
    await wrapper.get(".source-chip").trigger("click");
    await wrapper.get(".source-list li button").trigger("click");
    await flushPromises();
    expect(wrapper.get("[role=alert]").text()).toBe("Could not save");
    expect(wrapper.emitted("changed")).toBeUndefined();
  });
});

describe("the Metadata row in the Sources summary", () => {
  beforeEach(() => vi.clearAllMocks());

  it("shows the preferred source and the fields that disagree", async () => {
    vi.mocked(identity.fetchIdentitySources).mockResolvedValue({
      observations: [],
      versions: [],
      counters: {},
    } as unknown as identity.IdentitySources);
    vi.mocked(fields.fetchFieldSources).mockResolvedValue({
      developer: entry({ preferred: "provider:steam" }),
    });
    const wrapper = mount(GameSourcesPanel, { props: { gameId: "g1" } });
    await flushPromises();
    const row = wrapper.get(".metadata-row");
    expect(row.text()).toContain("Preferred: steam (1 field)");
    expect(row.text()).toContain("Developer");
  });

  it("names where each field comes from and flags a preference not shown", async () => {
    vi.mocked(identity.fetchIdentitySources).mockResolvedValue({
      observations: [],
      versions: [],
      counters: {},
    } as unknown as identity.IdentitySources);
    const one = (over: Partial<FieldSourceEntry> = {}) =>
      entry({ sources: [entry().sources[1]], ...over });
    vi.mocked(fields.fetchFieldSources).mockResolvedValue({
      developer: one({ preferred: "provider:rawg" }),
      release_date: one({ locked: true }),
    });
    const wrapper = mount(GameSourcesPanel, { props: { gameId: "g1" } });
    await flushPromises();
    const items = wrapper.findAll(".provenance li");
    expect(
      items.map((i) => [i.get(".field").text(), i.get(".from").text()]),
    ).toEqual([
      ["Developer", "igdb"],
      ["Release date", "igdb"],
    ]);
    expect(items[0].get(".pref-note").text()).toBe("rawg preferred, not shown");
    expect(items[0].classes()).toContain("warn");
    expect(items[1].find("[aria-label=Locked]").exists()).toBe(true);
  });

  it("adds no row when nothing is preferred or disputed", async () => {
    vi.mocked(identity.fetchIdentitySources).mockResolvedValue({
      observations: [],
      versions: [],
      counters: {},
    } as unknown as identity.IdentitySources);
    vi.mocked(fields.fetchFieldSources).mockResolvedValue({});
    const wrapper = mount(GameSourcesPanel, { props: { gameId: "g1" } });
    await flushPromises();
    expect(wrapper.find(".metadata-row").exists()).toBe(false);
  });
});
