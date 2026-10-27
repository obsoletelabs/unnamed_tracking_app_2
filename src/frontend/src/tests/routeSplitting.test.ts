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

it("keeps Settings sections in dynamic imports for opening or intent prefetch", () => {
  const settings = source("views/Settings.vue");
  const sections = settings.match(/components\/settings\/\w+\.vue/g) ?? [];
  expect(sections.length).toBeGreaterThan(25);
  for (const section of new Set(sections)) {
    const name = section.split("/").pop()!.replace(".vue", "");
    if (
      ["SettingsNav", "SettingsSearch", "SaveStatus", "ToggleButton"].includes(
        name,
      )
    )
      continue;
    expect(settings).toMatch(
      new RegExp(`const ${name} = lazySettingsSection\\(`),
    );
    expect(settings).toContain(`import("../components/settings/${name}.vue")`);
    expect(settings).not.toMatch(new RegExp(`import ${name} from`));
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
