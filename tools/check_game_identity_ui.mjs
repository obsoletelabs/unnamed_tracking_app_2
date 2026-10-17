// Umbrella library, exact-version search, versions panel and the manage-identity
// dialog against real APIs in a disposable account, at phone and desktop width.
import assert from "node:assert/strict";
import path from "node:path";

export async function checkGameIdentityUi({ browser, admin, origin, evidenceRoot, report }) {
  const ids = [];
  const post = async (url, data) => {
    const response = await admin.request.post(origin + url, { data });
    assert.ok(response.ok(), `${url} -> ${response.status()}`);
    return response.json().catch(() => null);
  };
  try {
    const stamp = Date.now();
    const make = async (title, platform, seconds) => {
      const game = await post("/api/game/create", { title, platform, source: "playnite", status: "PLAYING",
        folder_location: `identity-${stamp}-${ids.length}`, playtime_seconds: seconds });
      ids.push(game.id);
      return game.id;
    };
    const java = await make(`Craft ${stamp} Java Edition`, "PC", 7200);
    const bedrock = await make(`Craft ${stamp} Bedrock Edition`, "Xbox", 3600);
    const dungeons = await make(`Craft ${stamp} Dungeons`, "PC", 600);
    await post("/api/game-identity/merge", { source_id: bedrock, target_id: java });
    for (const width of [390, 1440]) {
      const context = await browser.newContext({ storageState: await admin.storageState(), viewport: { width, height: 900 } });
      try {
        await context.request.patch(origin + "/api/preferences", { data: { ui_welcome_completed: true } });
        const page = await context.newPage(), errors = [];
        page.on("pageerror", (error) => errors.push(String(error)));
        await page.goto(origin + "/games");
        await page.getByPlaceholder(/search your library/i).fill(`Craft ${stamp}`);
        await page.locator(".game-card-wrap").first().waitFor();
        const titles = await page.locator(".game-card-wrap .title").allInnerTexts();
        assert.equal(titles.length, 2, `umbrella once plus the distinct game at ${width}: ${titles}`);
        assert.ok(titles.some((t) => t.includes("Dungeons")));
        await page.getByPlaceholder(/search your library/i).fill(`Craft ${stamp} bedrock`);
        await page.locator("[data-testid=version-context]").waitFor();
        assert.equal(await page.locator(".game-card-wrap").count(), 1);
        assert.equal((await page.locator("[data-testid=version-context]").innerText()).trim(), "Bedrock Edition");
        await page.goto(origin + "/games/" + java);
        await page.getByRole("heading", { name: "Versions", exact: true }).waitFor();
        assert.equal((await page.locator("[data-testid=overall-playtime]").innerText()).trim(), "3h");
        assert.equal(await page.locator("[data-testid=version-row]").count(), 2);
        const boundary = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
        assert.ok(boundary <= 1, `no horizontal overflow at ${width}`);
        await page.getByRole("button", { name: /Manage identity/ }).focus();
        await page.keyboard.press("Enter");
        const dialog = page.getByRole("dialog");
        await dialog.waitFor();
        await dialog.getByRole("button", { name: "Sources", exact: true }).click();
        await dialog.getByText(/Each source keeps its own record/).waitFor();
        await page.keyboard.press("Escape");
        await dialog.waitFor({ state: "detached" });
        assert.deepEqual(errors, []);
        await page.screenshot({ path: path.join(evidenceRoot, `game-identity-${width}.png`) });
      } finally { await context.close(); }
    }
    report.game_identity = "umbrella, version search, versions panel and manage dialog at 390 and 1440 px";
  } finally {
    for (const id of ids) await admin.request.delete(origin + `/api/game/${id}`).catch(() => {});
  }
}
