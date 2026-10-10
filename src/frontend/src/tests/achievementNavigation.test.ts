import { expect, it, vi } from "vitest";
import { createSSRApp } from "vue";
import { renderToString } from "vue/server-renderer";
import { createMemoryHistory, createRouter } from "vue-router";
import AchievementDetail from "../views/AchievementDetail.vue";

vi.mock("../services/games", () => ({
  fetchGame: () => new Promise(() => {}),
  fetchGameAchievements: vi.fn(),
  listGameNoteSummaries: async () => [],
}));
vi.mock("../services/media", () => ({
  listGameScreenshots: async () => [],
  uploadGameScreenshots: vi.fn(),
  updateMediaItem: vi.fn(),
}));

it("keeps Games and Collections navigation available while achievement data loads", async () => {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/games", component: { render: () => null } },
      { path: "/games/collections", component: { render: () => null } },
      {
        path: "/games/:gameId/achievements/:achievementId",
        component: AchievementDetail,
      },
    ],
  });
  await router.push("/games/review-game/achievements/review-achievement");
  const app = createSSRApp(AchievementDetail);
  app.use(router);
  const html = await renderToString(app);
  expect(html).toContain("Loading…");
  expect(html).toContain('aria-label="Games area"');
  expect(html).toContain('href="/games"');
  expect(html).toContain('href="/games/collections"');
});
