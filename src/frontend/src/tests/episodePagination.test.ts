// @vitest-environment happy-dom
import { mount } from "@vue/test-utils";
import { expect, it } from "vitest";
import EpisodeList, { type EpisodeVM } from "../components/EpisodeList.vue";

it("keeps the chosen page through episode saves and resets for a different ordered list", async () => {
  const episodes: EpisodeVM[] = Array.from({ length: 110 }, (_, index) => ({
    id: `season-a-${index}`,
    episodeNumber: index + 1,
    title: `Episode ${index + 1}`,
    description: null,
    airDate: null,
    stillUrl: null,
    watched: false,
    rating: null,
  }));
  const view = mount(EpisodeList, { props: { episodes, loading: false } });
  await view.get("select").setValue("1");
  await view.setProps({
    episodes: episodes.map((episode) => ({
      ...episode,
      watched: true,
      rating: 8,
      note: "Saved",
    })),
  });
  expect(view.get("select").element.value).toBe("1");
  expect(view.text()).toContain("Episode 51");
  await view.setProps({
    episodes: episodes.map((episode) => ({
      ...episode,
      id: episode.id.replace("season-a", "season-b"),
    })),
  });
  expect(view.get("select").element.value).toBe("0");
  view.unmount();
});
