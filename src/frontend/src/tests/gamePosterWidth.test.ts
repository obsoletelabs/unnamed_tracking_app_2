import { afterEach, expect, it, vi } from "vitest";
import { posterWidth, sizedAssetUrl } from "../utils/gameImages";

afterEach(() => vi.unstubAllGlobals());

it.each([
  undefined,
  { effectiveType: "4g" },
  { effectiveType: "3g" },
  { downlink: 1 },
])(
  "uses the prebuilt 400px poster on ordinary connections (%j)",
  (connection) => {
    vi.stubGlobal("navigator", { connection });
    expect(posterWidth()).toBe(400);
  },
);
it.each([
  { saveData: true },
  { effectiveType: "slow-2g" },
  { effectiveType: "2g" },
])(
  "allows smaller posters for explicit data saving or very slow connections (%j)",
  (connection) => {
    vi.stubGlobal("navigator", { connection });
    expect(posterWidth()).toBe(240);
  },
);
it("retains explicit thumbnail width overrides", () => {
  expect(sizedAssetUrl("/api/game/id/assets/key_art?w=400", 64)).toBe(
    "/api/game/id/assets/key_art?w=64",
  );
});
