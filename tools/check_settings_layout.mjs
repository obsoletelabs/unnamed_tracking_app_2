// Render the real Settings components with deliberately long public response
// fixtures. Provider connectivity and statistics acquisition are not validated.
import assert from "node:assert/strict";
import path from "node:path";

export async function checkSettingsLayout({ browser, admin, origin, evidenceRoot, report }) {
  const original = await (await admin.request.get(origin + "/api/preferences")).json();
  const title = "A Very Long Game Title: " + "UnbrokenTitle".repeat(12);
  try {
    for (const width of [320, 390, 1440]) {
      const context = await browser.newContext({ storageState: await admin.storageState(), viewport: { width, height: 1000 } });
      try {
        const theme = width === 1440 ? "dark" : "light";
        assert.equal((await context.request.patch(origin + "/api/preferences", { data: { ui_theme: theme, ui_welcome_completed: true } })).status(), 200);
        const page = await context.newPage(), errors = [];
        page.on("pageerror", error => errors.push(String(error)));
        await page.route("**/api/settings/provider-credentials", route => route.fulfill({ json: {
          RetroAchievements: { status: "connected", display_name: "LongPlayerName".repeat(12), library_games: 1234, last_synced_at: 1 },
          Steam: { status: "connected", display_name: "Review player", library_games: 27, last_synced_at: 1 },
          PlayStation: { status: "error", detail: "Connection unavailable" }, Xbox: { status: "not_configured" },
        } }));
        const stats = await (await context.request.get(origin + "/api/stats/overview")).json();
        stats.most_played = [{ id: "layout-fixture", title, playtime_seconds: 123456789 }];
        stats.top_tags = [{ label: "LongTagWithoutSpaces".repeat(12), count: 1234 }];
        await page.route("**/api/stats/overview", route => route.fulfill({ json: stats }));
        await page.goto(origin + "/settings?area=account&section=connections");
        await page.locator(".rows .row").first().waitFor();
        assert(await page.locator(".rows .row").evaluateAll(rows => rows.every(row => {
          const title = row.querySelector("strong").getBoundingClientRect();
          const info = row.querySelector(".info").getBoundingClientRect();
          return row.scrollWidth <= row.clientWidth + 1 && title.right <= info.right + 1 &&
            [...row.querySelectorAll(".pill,.btn")].every(control => {
              const r = control.getBoundingClientRect();
              return title.right <= r.left || title.left >= r.right || title.bottom <= r.top || title.top >= r.bottom;
            });
        })), "Provider names, status and controls fit without overlap");
        async function deepLinkHeading(name) {
          const y = await page.getByRole("heading", { name, exact: true, level: 2 }).evaluate(item => item.getBoundingClientRect().y);
          assert(y >= 0 && y < 800, "Initial Settings deep link shows its selected section");
        }
        await deepLinkHeading("Connections");
        await page.screenshot({ path: path.join(evidenceRoot, `connections-${width}.png`), fullPage: true });
        await page.getByRole("button", { name: "Manage", exact: true }).first().click();
        await page.waitForURL("**/settings?**section=metadata**");
        await page.getByRole("heading", { name: "Metadata", exact: true, level: 1 }).waitFor();
        await page.goto(origin + "/settings?area=administration&section=stats");
        await page.locator(".ranked-list li").waitFor();
        await deepLinkHeading("Server Stats");
        await page.locator(".breakdown-block").filter({ has: page.getByRole("heading", { name: "Top tags", exact: true }) }).getByRole("button", { name: "List", exact: true }).click();
        assert.equal(await page.locator(".ranked-title").getAttribute("title"), title, "Full clipped title remains available");
        assert(await page.locator(".ranked-list li").evaluate(row => row.scrollWidth <= row.clientWidth + 1 && row.firstElementChild.getBoundingClientRect().right < row.lastElementChild.getBoundingClientRect().left));
        assert(await page.locator(".plain-list li").evaluateAll(rows => rows.every(row => row.scrollWidth <= row.clientWidth + 1)));
        assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
        await page.locator(".ranked-list").scrollIntoViewIfNeeded();
        await page.screenshot({ path: path.join(evidenceRoot, `stats-${width}.png`), fullPage: true });
        assert.deepEqual(errors, []);
        report.passed.push(`Settings names/actions, ranked values, tag wrapping, Manage navigation and initial deep links: ${width}/${theme}`);
      } finally { await context.close(); }
    }
  } finally {
    assert.equal((await admin.request.patch(origin + "/api/preferences", { data: { ui_theme: original.ui_theme, ui_welcome_completed: original.ui_welcome_completed } })).status(), 200);
  }
}
