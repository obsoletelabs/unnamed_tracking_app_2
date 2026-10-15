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
    metadata: { description: "Offered description", genres: ["Drama"] },
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
      metadata: { genres: ["Drama"] },
    });
    expect((event[1] as MetadataCandidate).metadata).not.toHaveProperty(
      "description",
    );
  } finally {
    wrapper.unmount();
  }
});
