// Exercise actual library requests against a disposable backend. Only failed
// connections are injected; successful responses come from the application.
// Usage: UI_REVIEW_USERNAME=... UI_REVIEW_PASSWORD=... node tools/check_library_recovery.mjs plugins-root evidence-root origin
import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import { createRequire } from "node:module";
import path from "node:path";

const [pluginsRoot, evidenceRoot, origin] = process.argv.slice(2);
assert(pluginsRoot && evidenceRoot && origin && process.env.UI_REVIEW_USERNAME && process.env.UI_REVIEW_PASSWORD,
  "Supply the browser dependencies, evidence directory, disposable application and review credentials.");
const { chromium } = createRequire(path.resolve(pluginsRoot, "package.json"))("playwright");
await mkdir(evidenceRoot, { recursive: true });
const browser = await chromium.launch({ headless: true, args: ["--no-sandbox"],
  ...(process.env.UI_REVIEW_BROWSER_EXECUTABLE ? { executablePath: process.env.UI_REVIEW_BROWSER_EXECUTABLE } : {}),
});
const admin = await browser.newContext();
const records = [];
const report = { backend: "real", host_source_head: process.env.UI_REVIEW_HOST_HEAD ?? null, recovered_without_reload: [], screenshots: [] };
const libraries = [["game", "/games"], ["movie", "/movies"], ["tv", "/tv"], ["anime", "/anime"]];
try {
  const login = await admin.request.post(origin + "/api/auth/login", {
    data: { username_or_email: process.env.UI_REVIEW_USERNAME, password: process.env.UI_REVIEW_PASSWORD },
  });
  assert.equal(login.status(), 200, "Disposable account must sign in");
  for (const [kind] of libraries) {
    const title = `Recovery check ${kind} ${Date.now()}`;
    const created = await admin.request.post(`${origin}/api/${kind}/create`, {
      data: { title, ...(kind === "game" ? { folder_location: `recovery-${Date.now()}` } : {}) },
    });
    assert.equal(created.status(), 201, await created.text());
    records.push({ kind, id: (await created.json()).id, title });
  }
  for (const width of [390, 1440]) for (const [kind, route] of libraries) for (const signal of ["online", "focus"]) {
    const context = await browser.newContext({ storageState: await admin.storageState(), viewport: { width, height: 900 } });
    try {
      const page = await context.newPage();
      const errors = [];
      page.on("pageerror", error => errors.push(String(error)));
      let unavailable = true;
      await page.route(`**/api/${kind}/list*`, request => unavailable ? request.abort("connectionrefused") : request.continue());
      await page.goto(origin + route);
      await page.locator(".empty-state.error").waitFor();
      const identity = await page.evaluate(() => performance.timeOrigin);
      if (kind === "movie" && signal === "online") {
        const file = `unavailable-${width}.png`;
        await page.screenshot({ path: path.join(evidenceRoot, file), fullPage: true });
        report.screenshots.push(file);
      }
      unavailable = false;
      if (signal === "online") {
        await context.setOffline(true);
        await context.setOffline(false);
      } else {
        // A window focus event is sufficient when the backend was unavailable
        // while the device itself still had a working network connection.
        await page.evaluate(() => window.dispatchEvent(new Event("focus")));
      }
      await page.locator(".empty-state.error").waitFor({ state: "hidden" });
      const title = records.find(record => record.kind === kind).title;
      await page.getByText(title, { exact: true }).first().waitFor();
      assert.equal(await page.evaluate(() => performance.timeOrigin), identity, "Library recovery must preserve the loaded document");
      assert.equal(new URL(page.url()).pathname, route);
      assert.deepEqual(errors, []);
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), "Recovered library must fit the viewport");
      if (kind === "movie" && signal === "online") {
        const file = `recovered-${width}.png`;
        await page.screenshot({ path: path.join(evidenceRoot, file), fullPage: true });
        report.screenshots.push(file);
      }
      report.recovered_without_reload.push({ kind, width, signal });
    } finally {
      await context.close();
    }
  }
  await writeFile(path.join(evidenceRoot, "conformance.json"), JSON.stringify(report, null, 2) + "\n");
  console.log(JSON.stringify(report));
} finally {
  for (const { kind, id } of records) {
    await admin.request.delete(`${origin}/api/${kind}/delete/${id}`);
    await admin.request.delete(`${origin}/api/${kind}/${id}/purge`);
  }
  await admin.close();
  await browser.close();
}
