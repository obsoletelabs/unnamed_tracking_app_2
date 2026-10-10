// A real disposable game with provider achievement-response fixtures. This
// checks the page's rendering/navigation, not external achievement acquisition.
import assert from "node:assert/strict";
import path from "node:path";

export async function checkAchievementNavigation({ browser, admin, origin, evidenceRoot, report }) {
  const original = await (await admin.request.get(origin + "/api/preferences")).json();
  const response = await admin.request.post(origin + "/api/game/create", { data: { title: "Achievement review game", folder_location: `achievement-review-${Date.now()}` } });
  assert.equal(response.status(), 201);
  const game = await response.json();
  try {
    for (const width of [390, 1440]) {
      const context = await browser.newContext({ storageState: await admin.storageState(), viewport: { width, height: 1000 } });
      try {
        assert.equal((await context.request.patch(origin + "/api/preferences", { data: { ui_theme: width === 390 ? "light" : "dark", ui_welcome_completed: true } })).status(), 200);
        const page = await context.newPage(), errors = [];
        page.on("pageerror", error => errors.push(String(error)));
        let unlocked = true, unlockedAt = 1760000000;
        await page.route(`**/api/game/${game.id}/achievements`, route => route.fulfill({ json: [{ id: "review-unlock", name: "An achievement milestone", description: "Unlock date and shared navigation review.", unlocked, unlocked_at: unlockedAt, provider: "steam", icon_url: null }] }));
        const route = `/games/${game.id}/achievements/review-unlock`;
        async function navigation() {
          const group = page.getByRole("group", { name: "Games area", exact: true });
          await group.waitFor();
          assert.equal(await group.getByRole("link", { name: "Games", exact: true }).getAttribute("href"), "/games");
          assert.equal(await group.getByRole("link", { name: "Collections", exact: true }).getAttribute("href"), "/games/collections");
        }
        await page.goto(origin + route);
        await page.getByRole("heading", { name: "An achievement milestone", exact: true }).waitFor();
        await navigation();
        assert.match(await page.locator(".achievement-unlocked").innerText(), /^Unlocked\s+\S/);
        assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
        await page.screenshot({ path: path.join(evidenceRoot, `achievement-${width}.png`), fullPage: true });
        unlockedAt = null;
        await page.reload(); await page.locator(".achievement-unlocked").waitFor();
        assert.equal(await page.locator(".achievement-unlocked").innerText(), "Unlocked", "Provider unlock flags need no timestamp");
        unlocked = false;
        await page.reload(); await page.getByText("Not yet unlocked", { exact: true }).waitFor();
        await page.getByRole("button", { name: `Back to ${game.title}`, exact: false }).click();
        await page.waitForURL(`**/games/${game.id}`);
        await page.goto(origin + `/games/${game.id}/achievements/not-found`);
        await page.getByText("Achievement not found.", { exact: true }).waitFor(); await navigation();
        await page.route(`**/api/game/get/${game.id}`, r => r.fulfill({ status: 503, body: "Unavailable fixture" }));
        await page.goto(origin + route); await page.locator(".error-state").waitFor(); await navigation();
        assert.deepEqual(errors, []);
        report.passed.push(`Achievement shared navigation, spaced date, undated unlock, locked state, Back route and missing/error states: ${width}`);
      } finally { await context.close(); }
    }
  } finally {
    assert.equal((await admin.request.delete(origin + `/api/game/delete/${game.id}`)).status(), 204);
    assert.equal((await admin.request.patch(origin + "/api/preferences", { data: { ui_theme: original.ui_theme, ui_welcome_completed: original.ui_welcome_completed } })).status(), 200);
  }
}
