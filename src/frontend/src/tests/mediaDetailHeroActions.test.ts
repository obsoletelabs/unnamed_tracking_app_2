// @vitest-environment happy-dom
import { mount } from "@vue/test-utils";
import { describe, expect, it, vi } from "vitest";
import MediaDetailHero from "../components/MediaDetailHero.vue";
import { pluginSlots } from "../state/pluginExtensions";

vi.mock("../state/pluginExtensions", async () => {
  const { ref } = await import("vue");
  return { pluginSlots: ref([]) };
});
const slots = pluginSlots as unknown as { value: { slot: string }[] };

function mountHero() {
  return mount(MediaDetailHero, {
    props: {
      title: "Arthur Phillip",
      nativeTitle: "Movie",
      posterUrl: null,
      heroBackdropUrl: null,
      hasBackdrop: false,
      ratingOverall: null,
      favorite: false,
      mediaType: "movie",
      mediaId: "m1",
      badges: [],
      status: "plan_to_watch",
    },
    global: {
      stubs: {
        PluginExtensionSlot: {
          props: ["slotId", "context"],
          template:
            '<div class="slot-stub" :data-slot="slotId" :data-media="context.media_id" :data-type="context.media_type" />',
        },
        MediaExtrasPanel: true,
        MediaWatchLinks: true,
        RatingPicker: true,
      },
    },
  });
}

describe("MediaDetailHero plugin actions", () => {
  it("offers the media actions slot beside Edit with the media's context", () => {
    slots.value = [];
    const slot = mountHero().get(".slot-stub");
    expect(slot.attributes()).toMatchObject({
      "data-slot": "media.detail.actions",
      "data-media": "m1",
      "data-type": "movie",
    });
  });

  it("steps Edit back once the open-in button has somewhere to go", async () => {
    slots.value = [];
    const wrapper = mountHero();
    expect(wrapper.get(".edit-btn").classes()).not.toContain("secondary");
    wrapper.getComponent({ name: "MediaWatchLinks" }).vm.$emit("present", true);
    await wrapper.vm.$nextTick();
    expect(wrapper.get(".edit-btn").classes()).toContain("secondary");
  });

  it("keeps Edit prominent until a plugin adds a watch action", async () => {
    slots.value = [];
    const wrapper = mountHero();
    expect(wrapper.get(".edit-btn").classes()).not.toContain("secondary");
    slots.value = [{ slot: "media.detail.actions" }];
    await wrapper.vm.$nextTick();
    expect(wrapper.get(".edit-btn").classes()).toContain("secondary");
  });
});
