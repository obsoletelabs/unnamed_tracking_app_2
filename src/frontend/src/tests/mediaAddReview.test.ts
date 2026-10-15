// @vitest-environment happy-dom
import { mount } from "@vue/test-utils";
import { expect, it } from "vitest";
import MediaAddModal from "../components/MediaAddModal.vue";
import type { MetadataCandidate } from "../services/metadata";

it("reviews checked metadata in the real quick-add flow without losing personal inputs", async () => {
  const candidate: MetadataCandidate = {
    id: "one",
    rank: 0,
    title: "Chosen movie",
    external_id: "42",
    provider: "core.omdb",
    provider_name: "OMDb",
    providers: ["core.omdb"],
    provider_ids: { imdb: "tt123" },
    media_type: "movie",
    year: null,
    alternate_titles: [],
    assets: [],
    metadata: {
      title: "Canonical chosen movie",
      description: "Offered description",
      genres: ["Drama"],
      year: 2005,
      provider_ids: { ignored: "id" },
      links: [{ label: "Unsupported", url: "https://example.test" }],
      scores: { tvmaze: 85 },
    },
  };
  const wrapper = mount(MediaAddModal, {
    props: { candidate, enriching: false, saving: false, error: null },
    global: {
      stubs: {
        UiModal: { template: '<div><slot/><slot name="footer"/></div>' },
      },
    },
  });
  try {
    await wrapper.get('input[type="number"]').setValue("7");
    await wrapper
      .findAll("button")
      .find((button) => button.text() === "Review metadata before adding")!
      .trigger("click");
    await wrapper
      .findAll(".comparison-change")
      .find((row) => row.get("strong").text() === "Description")!
      .get('input[type="checkbox"]')
      .setValue(false);
    expect(wrapper.emitted("save")).toBeUndefined();
    const offeredLabels = wrapper
      .findAll(".comparison-change strong")
      .map((row) => row.text());
    for (const unsupported of [
      "Title",
      "Year",
      "Provider ids",
      "Links",
      "Provider scores (0–100)",
    ])
      expect(offeredLabels).not.toContain(unsupported);
    await wrapper
      .findAll("button")
      .find((button) => /^Apply \d/.test(button.text()))!
      .trigger("click");
    await wrapper.setProps({
      candidate: {
        ...candidate,
        metadata: {
          ...candidate.metadata,
          description: "Late provider update",
        },
      },
    });
    await wrapper
      .findAll("button")
      .find((button) => button.text() === "Add to library")!
      .trigger("click");
    const event = wrapper.emitted("save")![0]!;
    expect(event[0]).toMatchObject({ score: 7, status: "wishlist" });
    expect(event[1]).toMatchObject({
      provider_ids: { imdb: "tt123" },
      metadata: { title: "Canonical chosen movie", genres: ["Drama"] },
    });
    expect((event[1] as MetadataCandidate).metadata).not.toHaveProperty(
      "description",
    );
  } finally {
    wrapper.unmount();
  }
});
