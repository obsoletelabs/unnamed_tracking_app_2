// Inspect an actual production container started with an unavailable database.
// Supply its start timestamp so the real failure deadline is measured too.
import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import { createRequire } from "node:module";
import path from "node:path";

const [pluginsRoot, evidenceRoot, origin] = process.argv.slice(2);
const started = Date.parse(process.env.UI_REVIEW_STARTED_AT ?? "");
assert(pluginsRoot && evidenceRoot && origin && Number.isFinite(started), "Supply browser dependencies, evidence, production origin and UI_REVIEW_STARTED_AT.");
const { chromium } = createRequire(path.resolve(pluginsRoot, "package.json"))("playwright");
await mkdir(evidenceRoot, { recursive: true });
const browser = await chromium.launch({ headless: true, args: ["--no-sandbox"] });
const report = { host_source_head: process.env.UI_REVIEW_HOST_HEAD ?? null, real_production_backend: true, checks: [], screenshots: [] };
try {
  const page = await browser.newPage();
  const errors = [];
  page.on("pageerror", error => errors.push(String(error)));
  for (const width of [390, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    assert.equal((await page.goto(origin + "/games?tag=Puzzle")).status(), 200);
    await page.locator("#phase").filter({ hasText: "Waiting for database" }).waitFor();
    assert.equal(await page.locator("#title").innerText(), "Starting application");
    assert(await page.locator("#spinner").isVisible());
    assert.equal((await page.request.get(origin + "/_startup/startup.css")).status(), 200);
    const api = await page.request.get(origin + "/api/auth/me");
    assert.equal(api.status(), 503);
    assert.equal(api.headers()["retry-after"], "5");
    assert((await api.json()).detail.includes("starting"));
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
    const file = `waiting-${width}.png`;
    await page.screenshot({ path: path.join(evidenceRoot, file), fullPage: true });
    report.screenshots.push(file);
    report.checks.push({ width, phase: "WAITING_FOR_DATABASE", html_status: 200, api_status: 503 });
  }
  await page.locator("#phase").filter({ hasText: "Database failed" }).waitFor({ timeout: 135000 });
  report.failure_elapsed_seconds = (Date.now() - started) / 1000;
  assert(report.failure_elapsed_seconds <= 127, "Database failure must respect the elapsed deadline plus startup/probe/poll overhead");
  assert.equal(await page.locator("#title").innerText(), "Application failed");
  assert(await page.locator("details").evaluate(element => element.open));
  assert.match(await page.locator("#details").innerText(), /PostgreSQL did not become ready/);
  const status = await (await page.request.get(origin + "/_startup/status.json")).json();
  assert.equal(status.phase, "DATABASE_FAILED");
  assert.equal(status.database, "failed");
  for (const width of [390, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
    const file = `failed-${width}.png`;
    await page.screenshot({ path: path.join(evidenceRoot, file), fullPage: true });
    report.screenshots.push(file);
    report.checks.push({ width, phase: "DATABASE_FAILED", diagnostics_visible: true });
  }
  assert.deepEqual(errors, []);
  await writeFile(path.join(evidenceRoot, "conformance.json"), JSON.stringify(report, null, 2) + "\n");
  console.log(JSON.stringify(report));
} finally {
  await browser.close();
}
