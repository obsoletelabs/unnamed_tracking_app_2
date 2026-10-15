import { describe, expect, it } from "vitest";
import { ref } from "vue";
import { useMetadataComparison } from "../composables/useMetadataComparison";

function setup() {
  const fields = ref({
    title: "My title",
    description: "",
    runtime: 0,
    providerIds: { tvmaze: "old" },
  });
  const locked = ref(false);
  const comparison = useMetadataComparison(
    () => fields.value,
    () => ({
      title: { label: "Title", locked: locked.value },
      description: { label: "Description" },
      runtime: { label: "Runtime" },
      providerIds: { label: "Provider identity" },
    }),
  );
  return { fields, locked, comparison };
}

describe("staged metadata comparison", () => {
  it("keeps an explicit unchecked field unchecked when later enrichment changes its offer", () => {
    const { comparison } = setup();
    comparison.offer({ description: "Early" }, "one");
    comparison.toggle("description", false);
    comparison.offer({ description: "Late" }, "one");
    expect(comparison.changes.value).toMatchObject([
      { key: "description", offered: "Late", checked: false },
    ]);
  });
  it("does not refill a populated field the user deliberately clears during review", () => {
    const { fields, comparison } = setup();
    comparison.offer({ title: "Offered" }, "one");
    fields.value.title = "";
    expect(comparison.changes.value).toEqual([]);
    expect(comparison.reason.value).toContain("form title changed");
    comparison.apply();
    expect(fields.value.title).toBe("");
  });
  it("checks only empty editable fields, preserves real zero, and stages no writes before Apply", () => {
    const { fields, comparison } = setup();
    comparison.offer(
      {
        title: "Provider title",
        description: "Offered",
        runtime: 30,
        providerIds: { tvmaze: "new" },
      },
      "one",
    );
    expect(fields.value.description).toBe("");
    expect(
      comparison.changes.value.filter((c) => c.checked).map((c) => c.key),
    ).toEqual(["description"]);
    expect([...comparison.apply()]).toEqual(["description"]);
    expect(fields.value).toEqual({
      title: "My title",
      description: "Offered",
      runtime: 0,
      providerIds: { tvmaze: "old" },
    });
  });

  it("retains explicit unchecked choices through enrichment and rejects edits made after checking", () => {
    const { fields, comparison } = setup();
    comparison.offer({ description: "Early", title: "Provider" }, "one");
    comparison.toggle("description", false);
    comparison.toggle("title", true);
    fields.value.title = "New unsaved title";
    comparison.offer({ description: "Late", title: "Provider 2" }, "one");
    expect(comparison.reason.value).toContain("form title changed");
    expect(comparison.changes.value.every((c) => !c.checked)).toBe(true);
    comparison.apply();
    expect(fields.value.title).toBe("New unsaved title");
    expect(fields.value.description).toBe("");
  });

  it("rechecks protection at Apply and discards cancelled or replaced choices", () => {
    const { fields, locked, comparison } = setup();
    comparison.offer({ title: "Provider" }, "one");
    comparison.toggle("title", true);
    locked.value = true;
    expect([...comparison.apply()]).toEqual([]);
    expect(fields.value.title).toBe("My title");
    comparison.offer({ description: "Unused" }, "one");
    comparison.clear();
    expect(comparison.changes.value).toEqual([]);
    comparison.offer({ description: "Next" }, "two");
    expect(comparison.changes.value[0]?.checked).toBe(true);
  });

  it("does not link provider identities merely because an unrelated field was checked", () => {
    const { fields, comparison } = setup();
    comparison.offer(
      { description: "Text", providerIds: { tvmaze: "new" } },
      "one",
    );
    comparison.apply();
    expect(fields.value.providerIds).toEqual({ tvmaze: "old" });
    comparison.offer({ providerIds: { tvmaze: "new" } }, "one");
    comparison.toggle("providerIds", true);
    expect([...comparison.apply()]).toEqual(["providerIds"]);
    expect(fields.value.providerIds).toEqual({ tvmaze: "new" });
  });
});
