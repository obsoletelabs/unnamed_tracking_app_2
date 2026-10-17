// Compiled movie progress UI against real APIs with disposable review records.
import assert from "node:assert/strict";
import path from "node:path";

export async function checkMovieProgressInput({ browser, member, origin, evidenceRoot, report }) {
  for (const width of [320, 390, 1440]) {
    const created = await member.request.post(origin + "/api/movie/create", { data: {
      title: "Movie progress review", status: "WISHLIST", note: "Keep review note",
      rewatches: 2, priority: "high",
    } });
    assert.equal(created.status(), 201);
    const id = (await created.json()).id;
    const context = await browser.newContext({ storageState: await member.storageState(),
      viewport: { width, height: 1000 } });
    try {
      await context.request.patch(origin + "/api/preferences", { data: {
        ui_theme: width === 1440 ? "dark" : "light", ui_welcome_completed: true,
      } });
      const page = await context.newPage(), errors = [], patches = [];
      page.on("pageerror", error => errors.push(String(error)));
      page.on("request", request => {
        if (request.method() === "PATCH" && request.url().endsWith("/api/movie/update/" + id))
          patches.push(request.postDataJSON());
      });
      const read = async () => {
        const response = await context.request.get(origin + "/api/movie/get/" + id);
        assert.equal(response.status(), 200); return response.json();
      };
      const saved = () => page.waitForResponse(response => response.request().method() === "PATCH"
        && response.url().endsWith("/api/movie/update/" + id));
      const resume = page.getByLabel("Left off at", { exact: true });
      async function enter(text, minutes, canonical) {
        const response = saved(); await resume.fill(text); await resume.press("Enter");
        assert.equal((await response).status(), 200);
        await page.waitForFunction(value => document.getElementById("movie-left-off")?.value === value, canonical);
        assert.equal((await read()).progress_minutes, minutes);
      }
      await page.goto(origin + "/movies/" + id); await resume.waitFor();
      assert.equal(await resume.getAttribute("inputmode"), "text");
      await enter("1h12m", 72, "1:12");
      assert.equal((await read()).status, "IN_PROGRESS");
      const afterEnter = patches.length;
      await page.getByRole("heading", { name: "Movie progress review", exact: true }).click();
      assert.equal(patches.length, afterEnter, "Blur after Enter must not save twice");
      await enter("1.5h", 90, "1:30");
      for (const text of ["1h75m", "2147483648"]) {
        const before = patches.length;
        await resume.fill(text); await resume.press("Enter");
        await page.getByText("Enter a valid time, such as 72, 1:12 or 1h12m.", { exact: true }).waitFor();
        assert.equal((await read()).progress_minutes, 90);
        assert.equal(patches.length, before, "Invalid input must not send a write");
      }
      assert.equal((await context.request.patch(origin + "/api/movie/update/" + id, { data: {
        runtime_minutes: 120, status: "BACKLOG",
      } })).status(), 200);
      await page.reload(); await resume.waitFor();
      await enter("2h10m", 120, "2:00");
      assert.equal((await read()).status, "BACKLOG", "Paused watches retain their status");
      assert.equal(await page.getByRole("progressbar", { name: "Watched so far" }).getAttribute("aria-valuenow"), "100");
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
      await page.screenshot({ path: path.join(evidenceRoot, "movie-progress-detail-" + width + ".png"), fullPage: false });
      await page.getByRole("button", { name: "✎ Edit", exact: true }).click();
      const dialog = page.getByRole("dialog", { name: "Edit movie", exact: true });
      const formPosition = dialog.getByLabel("Left off at", { exact: true });
      assert.equal(await formPosition.getAttribute("inputmode"), "text");
      await formPosition.fill("45m");
      await formPosition.scrollIntoViewIfNeeded();
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
      await page.screenshot({ path: path.join(evidenceRoot, "movie-progress-edit-" + width + ".png"), fullPage: false });
      const formSave = saved();
      await dialog.getByRole("button", { name: "Save changes", exact: true }).click();
      assert.equal((await formSave).status(), 200); await dialog.waitFor({ state: "hidden" });
      const edited = await read();
      assert.equal(edited.progress_minutes, 45); assert.equal(edited.note, "Keep review note");
      assert.equal(edited.rewatches, 2); assert.equal(edited.priority, "high");
      await enter("", null, "");
      await enter("30m", 30, "0:30");
      const completed = saved(); await page.locator(".status-select").selectOption("completed");
      assert.equal((await completed).status(), 200);
      assert.equal((await read()).progress_minutes, null);
      assert.equal((await read()).status, "WATCHED");
      assert.deepEqual(errors, []);
      report.passed.push("Real API detail/edit time text, status, range rejection, clamping, clearing and preserved fields: " + width);
    } finally {
      await context.close();
      assert.equal((await member.request.delete(origin + "/api/movie/delete/" + id)).status(), 204);
    }
  }
}
