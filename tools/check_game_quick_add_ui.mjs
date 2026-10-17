// Compiled game form against real saves; synthetic provider/artwork failures.
import assert from "node:assert/strict";
import path from "node:path";

const png = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/lXYAAAAASUVORK5CYII=", "base64");

export async function checkGameArtworkRetry({ member, origin, report }) {
  const page = await member.newPage();
  let creates = 0, attempts = 0;
  page.on("request", request => {
    if (request.method() === "POST" && request.url().endsWith("/api/game/create")) creates++;
  });
  await page.route("**/api/game/*/assets/key_art", async route => {
    attempts++;
    if (attempts === 1) await route.fulfill({ status: 503, body: "Synthetic artwork outage" });
    else await route.continue();
  });
  try {
    await page.goto(origin + "/games");
    await page.getByRole("button", { name: "+ Add game", exact: true }).first().click();
    const quick = page.getByRole("button", { name: "Full form", exact: true });
    if (await quick.count()) await quick.click();
    const dialog = page.getByRole("dialog", { name: "Add game", exact: true });
    await dialog.getByRole("tab", { name: "Basics", exact: true }).click();
    await dialog.getByLabel("Title", { exact: true }).fill("Artwork retry review");
    await dialog.getByLabel("Folder name", { exact: true }).fill("artwork-retry-" + Date.now());
    await dialog.getByRole("tab", { name: "Artwork", exact: true }).click();
    await dialog.locator('input[type="file"]').first().setInputFiles({ name: "review.png", mimeType: "image/png", buffer: png });
    await dialog.getByRole("tab", { name: "Advanced", exact: true }).click();
    await dialog.getByRole("button", { name: "Add game", exact: true }).click();
    await dialog.waitFor({ state: "hidden", timeout: 5000 });
    assert.equal(creates, 1, "Artwork failure does not keep a create form open");
    await page.getByText(/Game saved\. Artwork/).waitFor();
    await page.getByRole("button", { name: "Retry", exact: true }).click();
    await page.getByText("Artwork saved.", { exact: true }).waitFor();
    assert.equal(creates, 1, "Retry transfers artwork to the existing game only");
    assert.equal(attempts, 2);
    report.passed.push({ artwork_failure_preserves_game: true, retry_creates: 0 });
  } finally {
    await page.close();
  }
}

export async function checkGameQuickAdd({ member, origin, evidenceRoot, report }) {
  for (const width of [1440, 390, 320]) {
    const page = await member.newPage(), requests = [], errors = [];
    let creates = 0, selectionStarted, releaseSelection;
    const started = new Promise(resolve => selectionStarted = resolve);
    const released = new Promise(resolve => releaseSelection = resolve);
    page.on("pageerror", error => errors.push(error.message));
    page.on("request", request => {
      requests.push(request.url());
      if (request.method() === "POST" && request.url().endsWith("/api/game/create")) creates++;
    });
    const candidate = {
      id: "orbit-" + width, rank: 0, external_id: "orbit-" + width,
      title: "Orbit Archive Review " + width, year: 2026,
      provider: "review.optional", provider_name: "Review optional provider",
      providers: ["review.optional", "steam"],
      provider_ids: { "review.optional": "synthetic-" + width + "-" + "x".repeat(160), steam: "200000" + width },
      media_type: "game", alternate_titles: [],
      metadata: { title: "Orbit Archive Review " + width, description: "Synthetic provider selection for quick entry.", tags: ["Puzzle"], release_date: "2026-01-01" },
      assets: [],
    };
    const refined = { ...candidate, provider_ids: { ...candidate.provider_ids, igdb: "300000" + width } };
    const session = "quick-session-" + width;
    await page.route("**/api/metadata/providers", route => route.fulfill({ json: { providers: [{
      provider_id: "review.optional", plugin_id: "review.optional", name: "Review optional provider",
      media_types: ["game"], state: "healthy", configured_fields: {},
      operations: { search: "/search", metadata: "/metadata", media: "/media", health: "/health" }, configuration: [],
    }] } }));
    await page.route("**/api/metadata/sessions", route => route.fulfill({ json: {
      id: session, query: "Orbit", media_type: "game", state: "searching", results: [], last_event_id: 0,
    } }));
    await page.route("**/api/metadata/sessions/*/events?*", route => {
      const after = Number(new URL(route.request().url()).searchParams.get("after"));
      const events = after === 0 ? [
        { id: 1, event: "result_added", result: candidate },
        { id: 2, event: "search_completed" },
      ] : [{ id: 3, event: "selection_enrichment_completed" }];
      return route.fulfill({ contentType: "text/event-stream", body: events.map(event =>
        `id: ${event.id}\nevent: ${event.event}\ndata: ${JSON.stringify({ ...event, session_id: session })}\n\n`).join("") });
    });
    await page.route("**/api/metadata/sessions/*/selection", async route => {
      assert.equal(route.request().postDataJSON().candidate_id, candidate.id);
      selectionStarted();
      await released;
      await route.fulfill({ json: refined });
    });
    await page.route("**/api/metadata/sessions/*", async route => {
      if (route.request().method() === "DELETE") await route.fulfill({ status: 204 });
      else await route.fallback();
    });
    try {
      await page.setViewportSize({ width, height: 1000 });
      assert.equal((await member.request.patch(origin + "/api/preferences", { data: {
        ui_theme: width === 1440 ? "dark" : "light",
      } })).status(), 200);
      await page.goto(origin + "/games");
      if (width < 760) await page.getByRole("button", { name: "Library controls", exact: true }).click();
      await page.getByRole("button", { name: "+ Add game", exact: true }).first().waitFor();
      const before = requests.filter(url => url.includes("/api/game/list?")).length;
      await page.getByRole("button", { name: "+ Add game", exact: true }).first().click();
      const dialog = page.getByRole("dialog", { name: "Quick add game", exact: true });
      await dialog.getByRole("textbox", { name: "Title", exact: true }).waitFor();
      assert.equal(await dialog.getByRole("tablist").count(), 0);
      assert.equal(requests.filter(url => url.includes("/api/game/list?")).length, before);
      assert.equal(requests.filter(url => url.endsWith("/api/currency-codes")).length, 0);
      await dialog.getByRole("searchbox").fill("Orbit");
      await dialog.getByRole("button", { name: /Orbit Archive Review/ }).click();
      await started;
      assert(await dialog.getByRole("button", { name: "Add game", exact: true }).isDisabled(),
        "Cannot save before the selected identity response");
      await dialog.getByLabel("Title", { exact: true }).fill("Orbit Archive Review " + width + " edited");
      await dialog.getByLabel("Status").selectOption("wishlist");
      await dialog.getByLabel("Platform", { exact: true }).fill("Nintendo Switch");
      releaseSelection();
      await dialog.getByRole("button", { name: "Add game", exact: true }).waitFor();
      await page.waitForFunction(() => !document.querySelector('dialog .primary-button[type="submit"]').disabled);
      const box = await dialog.boundingBox();
      assert(box.width <= 640 && box.x >= 0 && box.x + box.width <= width + 1);
      assert(await dialog.evaluate(element => element.scrollWidth <= element.clientWidth + 1));
      await page.screenshot({ path: path.join(evidenceRoot, `quick-add-${width}.png`) });

      // Full form preserves the selected identity, query and manual tracking edits.
      await dialog.getByRole("button", { name: "Full form", exact: true }).click();
      const full = page.getByRole("dialog", { name: "Add game", exact: true });
      assert.equal(await full.getByLabel("Title", { exact: true }).inputValue(), "Orbit Archive Review " + width + " edited");
      assert.equal(await full.getByLabel("Status").inputValue(), "wishlist");
      assert.equal(await full.getByLabel("Platform", { exact: true }).inputValue(), "Nintendo Switch");
      await full.getByRole("button", { name: "Look up metadata", exact: true }).click();
      assert.equal(await full.getByRole("searchbox").inputValue(), "Orbit");
      await full.getByRole("tab", { name: "Basics", exact: true }).click();
      await page.screenshot({ path: path.join(evidenceRoot, `full-form-${width}.png`) });
      await full.getByRole("tab", { name: "Advanced", exact: true }).click();
      const saved = page.waitForResponse(response => response.url().endsWith("/api/game/create") && response.request().method() === "POST");
      await full.locator("form").evaluate(form => { form.requestSubmit(); form.requestSubmit(); form.requestSubmit(); });
      const response = await saved;
      assert.equal(response.status(), 201);
      const game = await response.json();
      await full.waitFor({ state: "hidden" });
      assert.equal(creates, 1, "Repeated submission creates only one game");
      assert.deepEqual(game.provider_ids, refined.provider_ids);
      assert.equal(game.title, "Orbit Archive Review " + width + " edited");
      assert.equal(game.platform, "Nintendo Switch");
      assert.equal(game.status, "WISHLIST");
      assert.equal(game.parent_game_id, null, "Creation does not automatically group copies");
      assert.deepEqual(errors, []);
      report.passed.push({ width, compact: true, deferred_choices: true,
        selection_wait: true, optional_provider_ids: true, full_form_preserves_edits: true,
        concurrent_submit_creates: 1, overflow: false, console_errors: 0 });
    } finally {
      releaseSelection();
      await page.close();
    }
  }
}

export async function checkManualQuickAdd({ member, origin, report }) {
  const page = await member.newPage();
  let creates = 0;
  page.on("request", request => {
    if (request.method() === "POST" && request.url().endsWith("/api/game/create")) creates++;
  });
  try {
    await page.setViewportSize({ width: 390, height: 1000 });
    await page.goto(origin + "/games");
    await page.getByRole("button", { name: "Library controls", exact: true }).click();
    const trigger = page.getByRole("button", { name: "+ Add game", exact: true }).first();
    await trigger.click();
    const compact = page.getByRole("dialog", { name: "Quick add game", exact: true });
    await compact.getByLabel("Title", { exact: true }).fill("Discarded quick draft");
    await page.keyboard.press("Escape");
    await compact.waitFor({ state: "hidden" });
    assert.equal(creates, 0);
    assert(await trigger.evaluate(element => document.activeElement === element), "Cancel returns focus to its trigger");

    let release, entered;
    const released = new Promise(resolve => release = resolve);
    const started = new Promise(resolve => entered = resolve);
    await page.route("**/api/game/create", async route => { entered(); await released; await route.continue(); });
    await trigger.click();
    await compact.getByLabel("Title", { exact: true }).fill("Manual Quick Entry review");
    await compact.getByLabel("Platform", { exact: true }).fill("Linux");
    const saved = page.waitForResponse(response => response.url().endsWith("/api/game/create") && response.request().method() === "POST");
    await compact.locator("form").evaluate(form => { form.requestSubmit(); form.requestSubmit(); form.requestSubmit(); });
    await started;
    assert(await compact.getByRole("button", { name: "Cancel", exact: true }).isDisabled());
    assert.equal(await compact.getByRole("button", { name: "Close dialog", exact: true }).count(), 0);
    await page.keyboard.press("Escape");
    assert(await compact.isVisible());
    release();
    const response = await saved;
    assert.equal(response.status(), 201);
    const game = await response.json();
    await compact.waitFor({ state: "hidden" });
    assert.equal(creates, 1);
    assert.equal(game.platform, "Linux");
    assert.equal(game.folder_location, "Manual-Quick-Entry-review");
    await page.unroute("**/api/game/create");

    await trigger.click();
    await compact.getByLabel("Title", { exact: true }).fill("Manual Quick Entry review");
    const collision = page.waitForResponse(response => response.url().endsWith("/api/game/create") && response.request().method() === "POST");
    await compact.getByRole("button", { name: "Add game", exact: true }).click();
    assert.equal((await collision).status(), 409);
    await compact.getByText(/already exists/).waitFor();
    await compact.getByRole("button", { name: "Full form", exact: true }).click();
    const full = page.getByRole("dialog", { name: "Add game", exact: true });
    await full.getByLabel("Folder name", { exact: true }).fill("manual-reviewed-copy");
    await full.getByRole("tab", { name: "Advanced", exact: true }).click();
    const separated = page.waitForResponse(response => response.url().endsWith("/api/game/create") && response.request().method() === "POST");
    await full.getByRole("button", { name: "Add game", exact: true }).click();
    assert.equal((await separated).status(), 201);
    await full.waitFor({ state: "hidden" });

    const before = creates;
    await trigger.click();
    await compact.getByLabel("Title", { exact: true }).fill("都市建设");
    await compact.getByRole("button", { name: "Add game", exact: true }).click();
    await full.getByText("Folder name is required.", { exact: true }).waitFor();
    assert.equal(await full.getByLabel("Title", { exact: true }).inputValue(), "都市建设");
    assert.equal(creates, before, "Missing generated folders are reviewed before creation");
    await full.getByRole("button", { name: "Cancel", exact: true }).click();
    report.passed.push({ manual_compact_save: true, cancel_restores_focus: true,
      pending_save_prevents_dismissal: true, concurrent_compact_submit_creates: 1,
      folder_collision_review: true, explicit_separate_copy: true, unicode_folder_review: true });
  } finally {
    await page.close();
  }
}
