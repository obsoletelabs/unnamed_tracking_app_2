// Real owned-game API records exercise duplicate review; no external provider
// acquisition is claimed. Plugin import replays have separate backend coverage.
import assert from "node:assert/strict";
import path from "node:path";

export async function checkGameDuplicates({ browser, admin, origin, evidenceRoot, report }) {
  const original = await (await admin.request.get(origin + "/api/preferences")).json();
  const ids = [];
  try {
    for (const width of [320, 390, 1440]) {
      const title = `Duplicate review ${width}: a long title with café and 日本語`;
      const pair = [];
      for (const [source, provider, identity] of [
        ["Steam", "steam", "620"],
        ["Plugin store with a long source label", "epic", "namespace:" + "e".repeat(100)],
      ]) {
        const created = await admin.request.post(origin + "/api/game/create", { data: {
          title, source, platform: "PC", release_date: "2011-04-19", status: "MASTERED",
          folder_location: `duplicate-review-${width}-${provider}-${Date.now()}`,
          provider_ids: { [provider]: identity }, playtime_seconds: 123456,
        } });
        assert.equal(created.status(), 201);
        const game = await created.json();
        ids.push(game.id); pair.push(game.id);
      }
      const context = await browser.newContext({ storageState: await admin.storageState(), viewport: { width, height: 1000 } });
      try {
        assert.equal((await context.request.patch(origin + "/api/preferences", { data: { ui_theme: width === 1440 ? "dark" : "light", ui_welcome_completed: true } })).status(), 200);
        const page = await context.newPage(), errors = [];
        page.on("pageerror", error => errors.push(String(error)));
        await page.goto(origin + "/settings?section=library&tab=duplicates");
        await page.getByRole("heading", { name: "Possible duplicate games" }).waitFor();
        await page.locator(".duplicate-pair").waitFor();
        assert.equal(await page.locator(".duplicate-pair").count(), 1);
        assert.equal(await page.getByRole("link", { name: title, exact: true }).count(), 2);
        assert.deepEqual((await page.locator(".game-title").evaluateAll(links => links.map(link => link.getAttribute("href")))).sort(), pair.map(id => `/games/${id}`).sort());
        assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
        await page.screenshot({ path: path.join(evidenceRoot, `duplicates-${width}.png`), fullPage: true });
        await page.route("**/api/game-duplicates/keep-both", route => route.fulfill({ status: 503, body: "Failure fixture" }));
        await page.getByRole("button", { name: "Keep both", exact: true }).click();
        await page.getByRole("alert").waitFor();
        assert.equal(await page.locator(".duplicate-pair").count(), 1, "Failed save retains the pair for retry");
        await page.unroute("**/api/game-duplicates/keep-both");
        await page.getByRole("button", { name: "Keep both", exact: true }).click();
        await page.getByText("No possible duplicates to review.", { exact: true }).waitFor();
        await page.reload();
        await page.getByText("No possible duplicates to review.", { exact: true }).waitFor();
        const renamedIds = [];
        for (const name of ["My custom game title", "Provider game title"]) {
          const created = await context.request.post(origin + "/api/game/create", { data: {
            title: `${name} ${width}`, folder_location: `identity-review-${width}-${Date.now()}`,
            provider_ids: { igdb: String(width) },
          } });
          assert.equal(created.status(), 201);
          const game = await created.json(); ids.push(game.id); renamedIds.push(game.id);
        }
        await page.getByRole("button", { name: "Check again", exact: true }).click();
        await page.getByText("A provider identity matches.", { exact: false }).waitFor();
        assert.equal(await page.locator(".duplicate-pair").count(), 1);
        await page.getByRole("button", { name: "Keep both", exact: true }).click();
        await page.getByText("No possible duplicates to review.", { exact: true }).waitFor();
        for (const id of renamedIds) assert.equal((await context.request.delete(origin + `/api/game/delete/${id}`)).status(), 204);
        assert.equal((await context.request.get(origin + "/api/game-duplicates")).status(), 200);
        for (const id of pair) assert.equal((await context.request.get(origin + `/api/game/get/${id}`)).status(), 200);
        assert.deepEqual(errors, []);
        report.passed.push(`Real API comparison, long Unicode title/identity, retry, persisted Keep both, custom-title identity match and unchanged games: ${width}`);
      } finally { await context.close(); }
      for (const id of pair) assert.equal((await admin.request.delete(origin + `/api/game/delete/${id}`)).status(), 204);
    }
  } finally {
    for (const id of ids) await admin.request.delete(origin + `/api/game/delete/${id}`);
    assert.equal((await admin.request.patch(origin + "/api/preferences", { data: { ui_theme: original.ui_theme, ui_welcome_completed: original.ui_welcome_completed } })).status(), 200);
  }
}
