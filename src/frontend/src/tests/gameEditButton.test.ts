import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const styles = readFileSync(
  resolve(__dirname, "../styles/pages/game-detail-layout.css"),
  "utf8",
);

describe("game detail Edit button styling", () => {
  it("uses the same neutral styling whether a launch action is present or not", () => {
    expect(styles).toMatch(
      /\.game-detail-page \.edit-btn\s*\{[^}]*background:\s*transparent;[^}]*color:\s*var\(--ui-text\);/s,
    );
    expect(styles).not.toContain(":has(.launch-group) .edit-btn");
  });
});
