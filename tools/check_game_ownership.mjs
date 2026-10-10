// Explicit grouping/separation against real owned-game APIs in a disposable account.
import assert from "node:assert/strict";
import path from "node:path";

export async function checkGameOwnership({ browser, admin, origin, evidenceRoot, report }) {
  const ids = [];
  try {
    for (const width of [320, 390, 1440]) {
      const title = `Owned copies ${width}: a long title with café and 日本語`;
      const pair = [];
      for (const [source, seconds] of [["Steam", 600], ["Plugin store with a long source label", 1200]]) {
        const response = await admin.request.post(origin + "/api/game/create", { data: {
          title, source, platform: "PC", status: "PLAYING", notes: source + " personal notes",
          folder_location: `ownership-${width}-${pair.length}-${Date.now()}`, playtime_seconds: seconds,
        } });
        assert.equal(response.status(), 201);
        const game = await response.json(); ids.push(game.id); pair.push(game.id);
      }
      const context = await browser.newContext({ storageState: await admin.storageState(), viewport: { width, height: 1000 } });
      try {
        assert.equal((await context.request.patch(origin + "/api/preferences", { data: { ui_theme: width === 1440 ? "dark" : "light", ui_welcome_completed: true } })).status(), 200);
        const page = await context.newPage(), errors = [];
        page.on("pageerror", error => errors.push(String(error)));
        await page.goto(origin + "/games");
        await page.getByRole("link", { name: "Review possible duplicate", exact: true }).first().waitFor();
        assert.equal(await page.locator(".game-card-wrap").count(), 2);
        await page.goto(origin + "/settings?section=library&tab=duplicates");
        await page.getByRole("button", { name: "Group owned copies", exact: true }).click();
        let dialog = page.getByRole("dialog", { name: "Group owned copies", exact: true });
        await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
        assert.equal(await page.locator(".duplicate-pair").count(), 1);
        await page.getByRole("button", { name: "Group owned copies", exact: true }).click();
        await dialog.getByLabel("Main game", { exact: true }).selectOption(pair[0]);
        await dialog.getByLabel("Owned copy", { exact: true }).selectOption(pair[1]);
        assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
        await page.screenshot({ path: path.join(evidenceRoot, `ownership-confirm-${width}.png`), fullPage: true });
        await page.route("**/api/game-ownership/group", route => route.fulfill({ status: 503, body: "Failure fixture" }));
        await dialog.getByRole("button", { name: "Group copies", exact: true }).click();
        await dialog.getByRole("alert").waitFor();
        assert.equal(await page.locator(".duplicate-pair").count(), 1);
        await page.unroute("**/api/game-ownership/group");
        await dialog.getByRole("button", { name: "Group copies", exact: true }).click();
        await page.getByText("No possible duplicates to review.", { exact: true }).waitFor();
        await page.goto(origin + "/games");
        await page.getByText("2 owned copies", { exact: true }).waitFor();
        assert.equal(await page.locator(".game-card-wrap").count(), 1);
        await page.goto(origin + "/games?provider=" + encodeURIComponent("Plugin store with a long source label"));
        await page.getByText("2 owned copies", { exact: true }).waitFor();
        assert.equal(await page.locator(".game-card-wrap").count(), 1, "A child store filter retains the main entry");
        await page.goto(origin + `/games/${pair[0]}`);
        const panel = page.locator(".owned-copies");
        await panel.getByText("2 owned copies · 30 min reported across copies", { exact: true }).waitFor();
        assert.equal(await panel.getByRole("link").count(), 2);
        assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
        await page.screenshot({ path: path.join(evidenceRoot, `ownership-main-${width}.png`), fullPage: true });
        await page.goto(origin + `/games/${pair[1]}`);
        await panel.getByRole("link", { name: "main game", exact: true }).waitFor();
        await panel.getByRole("button", { name: "Separate copy", exact: true }).click();
        dialog = page.getByRole("dialog", { name: "Separate owned copy", exact: true });
        await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
        await page.route("**/api/game-ownership/separate", route => route.fulfill({ status: 503, body: "Failure fixture" }));
        await panel.getByRole("button", { name: "Separate copy", exact: true }).click();
        await dialog.getByRole("button", { name: "Separate copy", exact: true }).click();
        await panel.getByRole("alert").waitFor();
        assert.equal(await panel.getByRole("link", { name: "main game", exact: true }).count(), 1);
        await page.unroute("**/api/game-ownership/separate");
        await panel.getByRole("button", { name: "Separate copy", exact: true }).click();
        await dialog.getByRole("button", { name: "Separate copy", exact: true }).click();
        await panel.getByRole("button", { name: "Add owned copy", exact: true }).waitFor();
        await page.goto(origin + "/games");
        await page.locator(".game-card-wrap").first().waitFor();
        assert.equal(await page.locator(".game-card-wrap").count(), 2);
        for (const id of pair) {
          const game = await (await context.request.get(origin + `/api/game/get/${id}`)).json();
          assert.equal(game.parent_game_id, null);
          assert.equal(game.notes, game.source + " personal notes");
        }
        assert.deepEqual(errors, []);
        report.passed.push(`Real API explicit grouping, cancellation/retry, one library entry, child source filter, main/copy navigation, reversible separation and preserved notes: ${width}`);
      } finally { await context.close(); }
      for (const id of pair) assert.equal((await admin.request.delete(origin + `/api/game/delete/${id}`)).status(), 204);
    }
  } finally {
    for (const id of ids) await admin.request.delete(origin + `/api/game/delete/${id}`);
  }
}
