import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

// Guards the final Rosefall wording pass (#154). It covers only the reviewed
// workflows; the rest of the app keeps its own copy until it is next touched.
const read = (path: string) =>
  readFileSync(resolve(__dirname, "..", path), "utf8");

const REVIEWED_ERROR_SURFACES = [
  "components/settings/MediaTrashSection.vue",
  "components/settings/ProfileSection.vue",
  "components/settings/CalendarSettingsSection.vue",
  "components/settings/NotificationSettingsSection.vue",
  "components/settings/AniListImportSection.vue",
  "components/settings/MediaRefreshSection.vue",
  "components/game/GameQuickEditModal.vue",
  "components/GameFormModal.vue",
  "components/MovieFormModal.vue",
  "components/TVShowFormModal.vue",
  "components/AnimeFormModal.vue",
];

describe("selective wording review", () => {
  it.each(REVIEWED_ERROR_SURFACES)(
    "%s reports actionable 'Could not' errors",
    (file) => {
      const source = read(file);
      expect(source).not.toMatch(/"Failed to [^"]*"/);
      expect(source).not.toContain('"Lost the connection"');
      expect(source).toMatch(/"Could not [^"]*\.?"/);
    },
  );

  it("keeps server detail ahead of the fallback", () => {
    // The fallback only replaces a non-Error value; real diagnostics win.
    expect(read("components/settings/MediaTrashSection.vue")).toMatch(
      /e instanceof Error \? e\.message : "Could not restore the title\."/,
    );
  });

  it("uses sentence case for editor titles and actions", () => {
    const game = read("components/GameFormModal.vue");
    expect(game).toContain('"Edit game"');
    expect(game).toContain('"Quick add game"');
    expect(game).toContain('"Save changes"');
    expect(game).toContain("Delete game");
    expect(game).not.toMatch(
      /"(Edit|Quick Add|Add) Game"|Save Changes|Delete Game/,
    );
    const shell = read("components/MediaFormShell.vue");
    expect(shell).toContain('"Save changes"');
    expect(shell).not.toContain("Save Changes");
    expect(read("components/TVShowFormModal.vue")).toContain('noun="TV show"');
    expect(read("components/AnimeFormModal.vue")).toContain('noun="anime"');
    expect(read("components/MovieFormModal.vue")).toContain('noun="movie"');
  });

  it("keeps destructive trash confirmations explicit", () => {
    const trash = read("components/settings/MediaTrashSection.vue");
    expect(trash).toContain("This can't be undone.");
    expect(trash).toContain('confirmLabel: "Delete forever"');
  });

  it("leaves Classic with presentation only, not its own copy", () => {
    const classic = read("styles/classic.css");
    expect(classic).not.toMatch(/content:\s*"[A-Za-z]{4,}/);
  });
});
