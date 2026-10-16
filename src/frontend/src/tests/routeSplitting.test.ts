import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { expect, it } from "vitest";

const source = (path: string) =>
  readFileSync(resolve(__dirname, "..", path), "utf8");

it("loads every routed view on demand", () => {
  const router = source("router/index.ts");
  const components = router.match(/component:\s*[^,\n]+/g) ?? [];
  expect(components.length).toBeGreaterThan(20);
  for (const component of components)
    expect(component).toMatch(/component:\s*\(\)\s*=>\s*import\(/);
});

it("fetches a Settings section only when it is opened", () => {
  const settings = source("views/Settings.vue");
  const sections = settings.match(/components\/settings\/\w+\.vue/g) ?? [];
  expect(sections.length).toBeGreaterThan(25);
  for (const section of new Set(sections)) {
    const name = section.split("/").pop()!.replace(".vue", "");
    if (["SettingsNav", "SaveStatus", "ToggleButton"].includes(name)) continue;
    expect(settings).toMatch(
      new RegExp(
        `const ${name} = defineAsyncComponent\\(\\s*\\(\\) => import\\(`,
      ),
    );
  }
});

it("keeps rarely opened Game detail panels and dialogs out of the route chunk", () => {
  const detail = source("views/GameDetail.vue");
  for (const name of [
    "GameFormModal",
    "ArchiveEditDialog",
    "GameMediaPanel",
    "GameArchivesPanel",
    "GameNotesPanel",
    "GameStatsPanel",
    "GameAccountsPanel",
    "GameWorldsPanel",
  ])
    expect(detail).toMatch(
      new RegExp(`const ${name} = defineAsyncComponent\\(`),
    );
});
