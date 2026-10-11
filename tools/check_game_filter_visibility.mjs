// Issue59 against compiled UI and real APIs in a disposable member account.
import assert from "node:assert/strict";
import path from "node:path";

export async function checkGameFilterVisibility({ browser, member, origin, evidenceRoot, report }) {
  const ids = [];
  try {
    for (let index = 0; index < 20; index++) {
      const response = await member.request.post(origin + "/api/game/create", { data: {
        title: "Filter review game " + index, platform: "PC", status: "PLAYING",
        tags: ["Puzzle", "RPG"], folder_location: "filters-" + Date.now() + "-" + index,
      } });
      assert.equal(response.status(), 201); ids.push((await response.json()).id);
    }
    for (const width of [320, 390, 1440]) {
      const context = await browser.newContext({ storageState: await member.storageState(),
        viewport: { width, height: 1000 } });
      try {
        await context.request.patch(origin + "/api/preferences", { data: {
          ui_theme: width === 1440 ? "dark" : "light", ui_welcome_completed: true,
        } });
        const page = await context.newPage(), errors = [];
        let documents = 0;
        page.on("pageerror", error => errors.push(String(error)));
        page.on("request", request => { if (request.isNavigationRequest() && request.frame() === page.mainFrame()) documents++; });
        const panel = page.locator(".advanced-panel");
        const filters = page.getByRole("button", { name: /^Filters/ });
        const url = () => new URL(page.url());
        async function showControls() {
          await page.getByRole("heading", { name: "Games", exact: true }).waitFor();
          if (width > 760) return;
          await page.getByRole("button", { name: /^(Library controls|Hide controls)$/ }).waitFor();
          const controls = page.getByRole("button", { name: "Library controls", exact: true });
          if (await controls.isVisible()) await controls.click();
        }
        await page.goto(origin + "/games?filters=1&campaign=review#review");
        await showControls();
        await panel.waitFor();
        await filters.click();
        await panel.waitFor({ state: "hidden" });
        await page.waitForURL(value => value.searchParams.get("filters") === "0");
        assert.equal(url().searchParams.get("campaign"), "review"); assert.equal(url().hash, "#review");
        await page.reload(); await page.locator(".game-card-wrap").first().waitFor();
        await showControls();
        assert.equal(await panel.count(), 0, "Empty closed filters remain closed after reload");
        await filters.click(); await panel.waitFor();
        await panel.getByRole("button", { name: "Puzzle", exact: true }).click();
        const rpg = panel.getByRole("button", { name: "RPG", exact: true });
        await rpg.scrollIntoViewIfNeeded();
        const beforeScroll = await page.evaluate(() => scrollY), beforeDocuments = documents;
        await rpg.click();
        await page.waitForURL(value => value.searchParams.getAll("tag").length === 2);
        const afterScroll = await page.evaluate(() => scrollY);
        assert(Math.abs(afterScroll - beforeScroll) <= 2, "Changing a genre preserves viewport");
        assert.equal(documents, beforeDocuments, "Changing filters does not reload the document");
        await filters.click(); await panel.waitFor({ state: "hidden" });
        await page.waitForURL(value => value.searchParams.get("filters") === "0");
        assert.deepEqual(url().searchParams.getAll("tag"), ["Puzzle", "RPG"]);
        assert.equal(url().hash, "#review");
        await page.reload(); await page.locator(".game-card-wrap").first().waitFor();
        await showControls();
        assert.equal(await panel.count(), 0, "Active closed filters remain closed after reload");
        assert.deepEqual(url().searchParams.getAll("tag"), ["Puzzle", "RPG"]);
        assert.equal((await page.locator(".page-head .sub").innerText()).trim(), "20 games");
        assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
        await page.screenshot({ path: path.join(evidenceRoot, "filters-closed-" + width + ".png"), fullPage: false });
        // Legacy links still open; browser history restores closed/open markers.
        await page.goto(origin + "/games?tag=Puzzle");
        await showControls();
        await panel.waitFor();
        await page.goBack(); await page.locator(".game-card-wrap").first().waitFor();
        await showControls();
        assert.equal(await panel.count(), 0);
        assert.equal(url().searchParams.get("filters"), "0");
        await page.goForward(); await showControls(); await panel.waitFor();
        assert.equal(await panel.getByRole("button", { name: "Puzzle", exact: true }).getAttribute("aria-pressed"), "true");
        await page.locator(".game-card-wrap").first().waitFor();
        assert.equal((await page.locator(".page-head .sub").innerText()).trim(), "20 games");
        await page.screenshot({ path: path.join(evidenceRoot, "filters-legacy-open-" + width + ".png"), fullPage: false });
        // An explicit empty closed URL overrides saved tags rather than restoring them.
        await page.goto(origin + "/games?filters=0");
        await page.locator(".game-card-wrap").first().waitFor();
        await showControls();
        assert.equal(await panel.count(), 0);
        assert.deepEqual(url().searchParams.getAll("tag"), []);
        assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
        assert.deepEqual(errors, []);
        report.passed.push("Empty/active closed reload, tags/hash/unrelated query, no reload/scroll jump, legacy links and history: " + width);
        report.scroll.push({ width, before: beforeScroll, after: afterScroll });
      } finally { await context.close(); }
    }
  } finally { for (const id of ids) await member.request.delete(origin + "/api/game/delete/" + id); }
}
