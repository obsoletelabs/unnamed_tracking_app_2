// Exercise both Steam import entry points using explicit provider-response fixtures.
// Authentication, settings routing and UI rendering use the disposable application.
import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import { createRequire } from "node:module";
import path from "node:path";

const [pluginsRoot, evidenceRoot, origin] = process.argv.slice(2);
assert(pluginsRoot && evidenceRoot && origin && process.env.UI_REVIEW_USERNAME && process.env.UI_REVIEW_PASSWORD,
  "Supply browser dependencies, an evidence directory, the disposable app and review credentials.");
const { chromium } = createRequire(path.resolve(pluginsRoot, "package.json"))("playwright");
await mkdir(evidenceRoot, { recursive: true });
const browser = await chromium.launch({ headless: true, args: ["--no-sandbox"] });
const admin = await browser.newContext();
const report = { host_source_head: process.env.UI_REVIEW_HOST_HEAD ?? null,
  provider_import_fixture: true, checks: [], screenshots: [] };
const ids = Array.from({ length: 6 }, (_, i) => `00000000-0000-4000-8000-${String(i + 1).padStart(12, "0")}`);
try {
  const login = await admin.request.post(origin + "/api/auth/login", {
    data: { username_or_email: process.env.UI_REVIEW_USERNAME, password: process.env.UI_REVIEW_PASSWORD },
  });
  assert.equal(login.status(), 200, "Disposable account must sign in");
  for (const width of [390, 1440]) for (const section of ["connections", "sources"]) {
    const context = await browser.newContext({ storageState: await admin.storageState(), viewport: { width, height: 1000 } });
    try {
      const page = await context.newPage();
      const errors = [], batches = [];
      page.on("pageerror", error => errors.push(String(error)));
      await page.route("**/api/settings/provider-credentials", route => route.fulfill({ json: {
        Steam: { status: "connected", library_games: 6, display_name: "Steam import test", last_synced_at: null },
      } }));
      await page.route("**/api/library-sync/steam**", async route => {
        const url = new URL(route.request().url());
        if (url.pathname.endsWith("/achievements")) {
          const batch = route.request().postDataJSON();
          batches.push(batch);
          return batches.length === 1
            ? route.fulfill({ json: { achievements_synced: 3, achievements_unavailable: ["Unavailable progress"] } })
            : route.fulfill({ status: 502, json: { detail: "Injected provider outage" } });
        }
        if (url.pathname.endsWith("/wishlist")) return route.fulfill({ json: { added: 0, game_ids: [] } });
        assert.equal(url.searchParams.get("achievements"), "later");
        await route.fulfill({ json: { games_added: 6, games_updated: 0, games: [], achievements_synced: 0,
          achievement_game_ids: ids, status_game_ids: [ids[0]], enrich_game_ids: [] } });
      });
      await page.goto(`${origin}/settings?section=${section}`);
      const button = page.getByRole("button", { name: section === "connections" ? "Sync now" : "Import Library", exact: true }).first();
      await button.waitFor();
      if (section === "sources") await page.getByText("Loading providers…", { exact: true }).waitFor({ state: "hidden" });
      const identity = await page.evaluate(() => performance.timeOrigin);
      await button.click();
      const message = section === "connections" ? page.locator(".note").filter({ hasText: "Achievement progress" })
        : page.locator(".task-toast-meta").filter({ hasText: "achievement progress unavailable" });
      await message.waitFor();
      assert.match(await message.innerText(), /(?:unavailable for 2 games)/);
      assert.deepEqual(batches.map(batch => batch.game_ids.length), [5, 1]);
      assert.deepEqual(batches.map(batch => batch.status_game_ids), [[ids[0]], []]);
      assert.equal(await page.evaluate(() => performance.timeOrigin), identity);
      assert.deepEqual(errors, []);
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), "Import result must fit the viewport");
      await message.scrollIntoViewIfNeeded();
      const file = `${section}-${width}.png`;
      await page.screenshot({ path: path.join(evidenceRoot, file), fullPage: section === "connections" });
      report.screenshots.push(file);
      report.checks.push({ section, width, achievement_batch_sizes: [5, 1], unavailable_games: 2, document_preserved: true });
    } finally {
      await context.close();
    }
  }
  await writeFile(path.join(evidenceRoot, "conformance.json"), JSON.stringify(report, null, 2) + "\n");
  console.log(JSON.stringify(report));
} finally {
  await admin.close();
  await browser.close();
}
