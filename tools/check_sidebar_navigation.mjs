import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import { createRequire } from "node:module";
import path from "node:path";

const [pluginsRoot, evidenceRoot, origin] = process.argv.slice(2);
assert(pluginsRoot && evidenceRoot && origin && process.env.UI_REVIEW_USERNAME && process.env.UI_REVIEW_PASSWORD,
  "Supply browser dependencies, evidence directory, disposable app and review credentials.");
const { chromium } = createRequire(path.resolve(pluginsRoot, "package.json"))("playwright");
await mkdir(evidenceRoot, { recursive: true });
const browser = await chromium.launch({ args: ["--no-sandbox"] });
const admin = await browser.newContext();
const before = process.env.UI_REVIEW_EXPECT_OLD === "true";
const report = { host_source_head: process.env.UI_REVIEW_HOST_HEAD ?? null, before_fix: before, checks: [] };
try {
  const login = await admin.request.post(`${origin}/api/auth/login`, {
    data: { username_or_email: process.env.UI_REVIEW_USERNAME, password: process.env.UI_REVIEW_PASSWORD },
  });
  assert.equal(login.status(), 200);
  for (const width of [390, 1440]) {
    const context = await browser.newContext({ storageState: await admin.storageState(), viewport: { width, height: 1000 } });
    try {
      const page = await context.newPage(), errors = [];
      page.on("pageerror", error => errors.push(String(error)));
      for (const route of ["/games", "/games/collections"]) {
        await page.goto(origin + route);
        if (width === 390) await page.getByRole("button", { name: "Open menu", exact: true }).click();
        const entries = page.locator(".nav-children a.nav-item.active");
        await entries.first().waitFor();
        const active = await entries.evaluateAll(items => items.map(item => item.getAttribute("href")));
        const expected = before && route === "/games/collections" ? ["/games", route] : [route];
        assert.deepEqual(active, expected);
        if (!before) assert(await entries.first().getAttribute("aria-current") === "page");
        assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
        assert.deepEqual(errors, []);
        const screenshot = route === "/games/collections" ? `${before ? "before" : "after"}-${width}.png` : null;
        if (screenshot) await page.screenshot({ path: path.join(evidenceRoot, screenshot) });
        report.checks.push({ route, width, active_entries: active, page_errors: errors, screenshot });
      }
    } finally { await context.close(); }
  }
  await writeFile(path.join(evidenceRoot, before ? "before.json" : "conformance.json"), JSON.stringify(report, null, 2) + "\n");
  console.log(JSON.stringify(report));
} finally { await admin.close(); await browser.close(); }
