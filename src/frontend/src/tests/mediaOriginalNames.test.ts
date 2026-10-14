import { expect, it } from "vitest";
import { createSSRApp } from "vue";
import { renderToString } from "vue/server-renderer";
import MediaTile from "../components/MediaTile.vue";
import { originalName } from "../utils/copyMedia";

it("preserves Unicode names and retains names on legacy files without a storage prefix", () => {
  expect(originalName("abcdef12_capture.png", "東京 — boss.png")).toBe(
    "東京 — boss.png",
  );
  expect(originalName("abcdef12_capture.png", null)).toBe("capture.png");
  expect(originalName("a".repeat(32) + "_capture.png")).toBe("capture.png");
  expect(originalName("boss_encounter.png")).toBe("boss_encounter.png");
});

it("renders an edited label and the preserved filename separately, escaping filename markup", async () => {
  const item = {
    id: "owned-media",
    filename: "abcdef12_safe.png",
    original_filename: "東京 <script>.png",
    kind: "screenshot",
    url: "/api/game/owned/screenshots/screenshot/abcdef12_safe.png",
    tags: [],
    note: null,
    created_at: 1700000000,
    title: "Boss encounter",
  };
  const html = await renderToString(createSSRApp(MediaTile, { item }));
  expect(html).toContain("Boss encounter");
  expect(html).toContain("東京 &lt;script&gt;.png");
  expect(html).not.toContain("<script>");
  expect(html).not.toContain(">safe.png<");
});
