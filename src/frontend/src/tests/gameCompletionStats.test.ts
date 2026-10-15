// @vitest-environment happy-dom
import { mount } from "@vue/test-utils";
import { expect, it } from "vitest";
import GameStatsPanel from "../components/GameStatsPanel.vue";
import { mockGames } from "../data/mockGames";

it("shows a completionist estimate separately from personal playtime", () => {
  const playtime = mockGames[0].platforms.map(
    (platform) => platform.playtimeMinutes,
  );
  const game = {
    ...mockGames[0],
    timeToBeatHours: 10.5,
    timeToBeatCompletionistHours: 25.8,
  };
  const wrapper = mount(GameStatsPanel, {
    props: { game, changes: [], media: [], loading: false, error: null },
  });
  expect(wrapper.text()).toContain("Main story estimate");
  expect(wrapper.text()).toContain("10.5h");
  expect(wrapper.text()).toContain("Completionist estimate");
  expect(wrapper.text()).toContain("25.8h");
  expect(game.platforms.map((platform) => platform.playtimeMinutes)).toEqual(
    playtime,
  );
  wrapper.unmount();
});
